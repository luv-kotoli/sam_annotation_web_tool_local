"""Optional, in-process local inference; manual annotation has no Torch dependency."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from fastapi import HTTPException

from .store import encode

logger = logging.getLogger(__name__)
DEPENDENCIES = ("torch", "torchvision", "cv2", "numpy", "pandas", "yaml", "tqdm")


def model_paths(settings):
    checkpoint = Path(os.environ.get("LOCAL_PHASE_CHECKPOINT", str(settings.models / "phase.pt"))).expanduser().resolve()
    return checkpoint, settings.models / "resnet18-f37072fd.pth"


def availability(settings):
    checkpoint, backbone = model_paths(settings)
    missing = [name for name in DEPENDENCIES if importlib.util.find_spec(name) is None]
    files = [path.name for path in (checkpoint, backbone) if not path.is_file()]
    if missing or files:
        parts = []
        if missing:
            parts.append("安装 requirements-ai.txt 中的可选推理依赖")
        if files:
            parts.append("在模型目录准备权重：" + "、".join(files))
        return {"ready": False, "message": "本机阶段 AI 尚未配置完成。现在可以进行人工阶段和征象标注，模型配置完成后可使用自动分期。",
                "setup_detail": "；".join(parts), "missing_dependencies": missing, "missing_weights": files}
    return {"ready": True, "message": "模型文件和推理依赖已准备；首次分析会加载并验证权重。分析结果经确认后才写入阶段标注。"}


def fingerprint(paths):
    result = []
    for path in paths:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        result.append(digest.hexdigest())
    return tuple(result)


class AnalysisJobs:
    def __init__(self, store):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-phase")
        self.stop = threading.Event()
        self.engine = None
        self.loaded_identity = None
        self.submit_lock = threading.Lock()
        # Do not claim completion or silently replay interrupted work.
        with store.connect(write=True) as conn:
            conn.execute("UPDATE jobs SET status='interrupted',error='服务已重启，请重新提交分析',updated_at=? WHERE status IN ('queued','running')", (time.time(),))

    def close(self):
        self.stop.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        with self.store.connect(write=True) as conn:
            conn.execute("UPDATE jobs SET status='interrupted',error='服务已停止，请重新提交分析',updated_at=? WHERE status IN ('queued','running')", (time.time(),))

    def list(self):
        with self.store.connect() as conn:
            jobs = []
            for row in conn.execute("""SELECT jobs.id,jobs.video_id,jobs.status,jobs.progress,jobs.stage,jobs.error,jobs.created_at,
                    videos.filename,json_extract(jobs.result_json,'$.summary') AS summary_json
                    FROM jobs JOIN videos ON videos.id=jobs.video_id ORDER BY jobs.created_at DESC LIMIT 30"""):
                jobs.append({key: row[key] for key in ("id", "video_id", "filename", "status", "progress", "stage", "error", "created_at")} | {"summary": json.loads(row["summary_json"]) if row["summary_json"] else None})
        return {"ok": True, "jobs": jobs}

    def result(self, job_id):
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, "分析任务不存在")
            if not row["result_json"]:
                raise HTTPException(409, "分析结果尚未生成")
            return json.loads(row["result_json"])

    def submit(self, video_id):
        status = availability(self.store.settings)
        if not status["ready"]:
            raise HTTPException(503, status["message"])
        with self.submit_lock:
            paths = model_paths(self.store.settings)
            identity = fingerprint(paths)
            with self.store.connect(write=True) as conn:
                self.store.video(conn, video_id)
                if conn.execute("SELECT 1 FROM jobs WHERE video_id=? AND status IN ('queued','running')", (video_id,)).fetchone():
                    raise HTTPException(409, "此视频已有分析任务")
                if conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 8:
                    raise HTTPException(429, "本机任务队列已满，请稍后提交")
                job_id = uuid4().hex
                conn.execute("INSERT INTO jobs(id,video_id,status,created_at,updated_at) VALUES(?,?,'queued',?,?)", (job_id, video_id, time.time(), time.time()))
            self.executor.submit(self.run, job_id, video_id, paths, identity)
        return {"ok": True, "job_id": job_id}

    def update(self, job_id, **changes):
        with self.store.connect(write=True) as conn:
            conn.execute("UPDATE jobs SET " + ",".join(key + "=?" for key in changes) + ",updated_at=? WHERE id=?", (*changes.values(), time.time(), job_id))

    def run(self, job_id, video_id, paths, expected_identity):
        try:
            def check():
                if self.stop.is_set():
                    raise InterruptedError("本机服务正在停止，请重新提交分析")

            check()
            self.update(job_id, status="running", stage="loading_model")
            import torch
            from .phase_reference.config import Settings as PhaseSettings
            from .phase_reference.phase_engine import PhaseEngine
            identity = fingerprint(paths)
            if identity != expected_identity:
                raise RuntimeError("排队期间模型权重发生改变，请重新提交分析")
            device_name = os.environ.get("LOCAL_PHASE_DEVICE", "auto")
            if device_name == "auto":
                device_name = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
            if device_name not in {"cpu", "mps", "cuda"}:
                raise ValueError("LOCAL_PHASE_DEVICE 仅支持 auto、cpu、mps、cuda")
            if self.engine is None or self.loaded_identity != (identity, device_name):
                cfg = PhaseSettings(model_project=Path(__file__).parent / "phase_reference/model_project", checkpoint=paths[0], backbone=paths[1])
                self.engine = PhaseEngine(cfg, threading.Lock(), device=device_name)
                if fingerprint(paths) != identity:
                    self.engine = None
                    raise RuntimeError("模型加载期间权重文件发生改变，请重新提交")
                self.loaded_identity = (identity, device_name)
            with self.store.connect() as conn:
                video = self.store.video(conn, video_id)
                path = self.store.settings.videos / video["stored_name"]
            check()
            result = self.engine.infer(path, {"profile": "annotation", "video_id": str(video_id), "crop_strategy": "adaptive-fov-v1"},
                                       check, lambda progress, stage: self.update(job_id, progress=progress, stage=stage))
            check()
            result["video_sha256"] = video["sha256"]
            self.update(job_id, status="succeeded", stage="complete", progress=1, result_json=encode(result))
        except InterruptedError as error:
            self.update(job_id, status="interrupted", error=str(error))
        except Exception as error:
            logger.exception("Local phase inference failed")
            self.update(job_id, status="failed", error="本机分析失败：" + str(error)[:500])

    def apply(self, job_id, body):
        from .phase_reference.phase_prediction import clinical_quality, visible_side
        with self.store.connect(write=True) as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] != "succeeded":
                raise HTTPException(409, "任务尚未完成或已应用")
            video = self.store.video(conn, job["video_id"])
            if video["revision"] != body.get("expected_revision"):
                raise HTTPException(409, "标注已更新，请重新核对后应用")
            if self.store.rows(conn, video["id"], "finding"):
                raise HTTPException(409, "此视频已有征象标注，请保留人工结果；可下载 AI 结果进行对照。")
            before = self.store.rows(conn, video["id"], "phase")
            if before and body.get("replace_existing") is not True:
                raise HTTPException(409, "替换已有阶段需要明确确认")
            result = json.loads(job["result_json"])
            if result.get("video_sha256") != video["sha256"]:
                raise HTTPException(409, "分析结果与当前视频不匹配")
            output, cursor = [], 0.0
            for segment in sorted(result["segments"], key=lambda row: row["start_sec"]):
                start, end = segment["start_sec"], segment["end_sec"]
                if start < cursor:
                    raise HTTPException(422, "AI 输出的阶段存在重叠，未修改人工标注")
                if start > cursor:
                    output.append({"start_sec": str(cursor), "end_sec": str(start), "phase_label": "unused", "quality_level": "", "visible_side": "unknown"})
                output.append({"start_sec": str(start), "end_sec": str(end), "phase_label": "effective_observation", "quality_level": clinical_quality(segment["quality_pred"], result["model"]), "visible_side": visible_side(segment)})
                cursor = end
            if cursor < video["duration_seconds"]:
                output.append({"start_sec": str(cursor), "end_sec": str(video["duration_seconds"]), "phase_label": "unused", "quality_level": "", "visible_side": "unknown"})
            validated = [self.store.validate(video, "phase", {**row, "case_id": video["case_id"], "video_id": str(video["id"]),
                         "segment_id": "ai_" + uuid4().hex, "annotator_id": "AI", "notes": "AI 初步结果，待人工复核"}) for row in output]
            conn.execute("DELETE FROM annotations WHERE video_id=? AND table_name='phase'", (video["id"],))
            for row in validated:
                self.store.insert(conn, video["id"], "phase", row, source="ai")
            self.store.changed(conn, video["id"], "apply-ai", {"job_id": job_id, "before": before, "model": result["model"]})
            conn.execute("UPDATE jobs SET status='applied',updated_at=? WHERE id=?", (time.time(), job_id))
            return {"ok": True, "video": self.store.public_video(conn, self.store.video(conn, video["id"]))}
