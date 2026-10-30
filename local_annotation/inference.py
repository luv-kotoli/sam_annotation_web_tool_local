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
from .crops import crop_record

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
        self.dispatcher = threading.Thread(target=self.dispatch, name='local-analysis-dispatch', daemon=True)
        self.dispatcher.start()

    def close(self):
        self.stop.set()
        self.dispatcher.join()
        self.executor.shutdown(wait=True, cancel_futures=True)
        with self.store.connect(write=True) as conn:
            conn.execute("UPDATE jobs SET status='interrupted',error='服务已停止，请重新提交分析',updated_at=? WHERE status IN ('queued','running')", (time.time(),))

    def list(self):
        with self.store.connect() as conn:
            jobs = []
            for row in conn.execute("""SELECT jobs.id,jobs.video_id,jobs.status,jobs.progress,jobs.stage,jobs.error,jobs.created_at,
                    videos.filename,jobs.origin,json_extract(jobs.result_json,'$.summary') AS summary_json
                    FROM jobs JOIN videos ON videos.id=jobs.video_id ORDER BY jobs.created_at DESC LIMIT 30"""):
                jobs.append({key: row[key] for key in ("id", "video_id", "filename", "status", "progress", "stage", "error", "created_at", "origin")} | {"summary": json.loads(row["summary_json"]) if row["summary_json"] else None})
        return {"ok": True, "jobs": jobs}

    def result(self, job_id):
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, "分析任务不存在")
            if not row["result_json"]:
                raise HTTPException(409, "分析结果尚未生成")
            return json.loads(row["result_json"])

    def protected(self, conn, video_id):
        video = self.store.video(conn, video_id)
        if video['review_state'] == 'reviewed':
            return '病例已复核'
        if conn.execute("SELECT 1 FROM annotations WHERE video_id=? AND table_name IN ('phase','finding')", (video_id,)).fetchone():
            return '已有阶段或征象标注，保留现有结果'
        if conn.execute("SELECT 1 FROM jobs WHERE video_id=? AND status IN ('queued','running','succeeded')", (video_id,)).fetchone():
            return '已有分析任务或待确认结果'
        return None

    def batch(self, body):
        ids = body.get('video_ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(type(item) is not int for item in ids) or len(set(ids)) != len(ids):
            raise HTTPException(422, '请选择 1–100 个不同病例')
        items = []
        with self.store.connect(write=True) as conn:
            for video_id in ids:
                self.store.video(conn, video_id)
            for video_id in ids:
                reason = self.protected(conn, video_id)
                request = conn.execute('SELECT state FROM upload_analysis WHERE video_id=?', (video_id,)).fetchone()
                if not reason and request and request['state'] in {'pending', 'waiting_config', 'waiting_capacity'}:
                    reason = '已在分析等待队列'
                if reason:
                    items.append({'video_id': video_id, 'state': 'skipped', 'detail': reason})
                    continue
                conn.execute("""INSERT INTO upload_analysis(video_id,state,origin,updated_at) VALUES(?,'pending','batch',?)
                    ON CONFLICT(video_id) DO UPDATE SET state='pending',origin='batch',detail='',job_id=NULL,updated_at=excluded.updated_at""", (video_id, time.time()))
                items.append({'video_id': video_id, 'state': 'pending', 'detail': '已加入持久等待队列'})
        return {'ok': True, 'items': items}

    def dispatch_once(self):
        with self.store.connect() as conn:
            ids = [row[0] for row in conn.execute("SELECT video_id FROM upload_analysis WHERE state IN ('pending','waiting_config','waiting_capacity') ORDER BY updated_at LIMIT 100")]
        for video_id in ids:
            if self.stop.is_set():
                return
            try:
                with self.store.connect(write=True) as conn:
                    request = conn.execute('SELECT * FROM upload_analysis WHERE video_id=?', (video_id,)).fetchone()
                    if not request or request['state'] not in {'pending', 'waiting_config', 'waiting_capacity'}:
                        continue
                    enabled = conn.execute("SELECT value FROM local_meta WHERE key='auto_analyze_uploads'").fetchone()[0] == 'true'
                    reason = self.protected(conn, video_id)
                    if request['origin'] == 'upload' and not enabled:
                        reason = '上传后自动分析已关闭'
                    if reason:
                        conn.execute("UPDATE upload_analysis SET state='skipped',detail=?,updated_at=? WHERE video_id=?", (reason, time.time(), video_id))
                        continue
                self.submit(video_id, origin=request['origin'])
            except HTTPException as error:
                state = {503: 'waiting_config', 429: 'waiting_capacity', 409: 'skipped'}.get(error.status_code, 'failed')
                with self.store.connect(write=True) as conn:
                    conn.execute('UPDATE upload_analysis SET state=?,detail=?,updated_at=? WHERE video_id=?', (state, str(error.detail), time.time(), video_id))
            except Exception as error:
                logger.exception('Analysis dispatch failed')
                with self.store.connect(write=True) as conn:
                    conn.execute("UPDATE upload_analysis SET state='failed',detail=?,updated_at=? WHERE video_id=?", (str(error)[:500], time.time(), video_id))

    def dispatch(self):
        while not self.stop.wait(2):
            try:
                self.dispatch_once()
            except Exception:
                logger.exception('Analysis dispatcher will retry after a storage error')

    def submit(self, video_id, origin='manual'):
        status = availability(self.store.settings)
        if not status["ready"]:
            raise HTTPException(503, status["message"])
        with self.submit_lock:
            with self.store.connect() as conn:
                self.store.video(conn, video_id)
                if conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 8:
                    raise HTTPException(429, "本机任务队列已满，请稍后提交")
            paths = model_paths(self.store.settings)
            identity = fingerprint(paths)
            with self.store.connect(write=True) as conn:
                video = self.store.video(conn, video_id)
                if origin != 'manual':
                    if origin == 'upload' and conn.execute("SELECT value FROM local_meta WHERE key='auto_analyze_uploads'").fetchone()[0] != 'true':
                        raise HTTPException(409, '上传后自动分析已关闭')
                    reason = self.protected(conn, video_id)
                    if reason:
                        raise HTTPException(409, reason)
                if conn.execute("SELECT 1 FROM jobs WHERE video_id=? AND status IN ('queued','running')", (video_id,)).fetchone():
                    raise HTTPException(409, "此视频已有分析任务")
                if conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 8:
                    raise HTTPException(429, "本机任务队列已满，请稍后提交")
                job_id = uuid4().hex
                conn.execute("INSERT INTO jobs(id,video_id,status,base_revision,origin,model_identity_json,created_at,updated_at) VALUES(?,?,'queued',?,?,?,?,?)", (job_id, video_id, video['revision'], origin, encode(identity), time.time(), time.time()))
                conn.execute("UPDATE upload_analysis SET state='submitted',detail='',job_id=?,updated_at=? WHERE video_id=?", (job_id, time.time(), video_id))
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
            self.finish(job_id, video, result)
        except InterruptedError as error:
            self.update(job_id, status="interrupted", error=str(error))
        except Exception as error:
            logger.exception("Local phase inference failed")
            self.update(job_id, status="failed", error="本机分析失败：" + str(error)[:500])

    def finish(self, job_id, video, result):
        record = crop_record(result, video, job_id)
        with self.store.connect(write=True) as conn:
            conn.execute("UPDATE jobs SET status='succeeded',stage='complete',progress=1,result_json=?,updated_at=? WHERE id=?", (encode(result), time.time(), job_id))
            if record:
                conn.execute('''INSERT INTO crop_records(video_id,job_id,record_json,updated_at) VALUES(?,?,?,?)
                    ON CONFLICT(video_id) DO UPDATE SET job_id=excluded.job_id,record_json=excluded.record_json,updated_at=excluded.updated_at''', (video['id'], job_id, encode(record), time.time()))
            else:
                conn.execute('DELETE FROM crop_records WHERE video_id=?', (video['id'],))

    def apply(self, job_id, body):
        from .phase_reference.phase_prediction import clinical_quality, visible_side
        with self.store.connect(write=True) as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] != "succeeded":
                raise HTTPException(409, "任务尚未完成或已应用")
            video = self.store.video(conn, job["video_id"])
            if video["revision"] != body.get("expected_revision"):
                raise HTTPException(409, "标注已更新，请重新核对后应用")
            if job['base_revision'] is not None and video['revision'] != job['base_revision']:
                raise HTTPException(409, '提交分析后人工标注已发生变化，此结果仅供对照，请重新分析')
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
