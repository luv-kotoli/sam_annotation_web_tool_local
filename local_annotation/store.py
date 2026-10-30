"""Local persistence and optimistic concurrency for the extracted editor."""
from contextlib import contextmanager
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
import time
import threading
from uuid import uuid4

from fastapi import HTTPException

ASSETS = Path(__file__).parent / "assets"
TABLES = {
    "case": ("病例标注", "doctor_case_annotation.csv"),
    "phase": ("视频阶段标注", "doctor_video_phase_annotation.csv"),
    "finding": ("有效片段征象标注", "doctor_effective_clip_finding_annotation.csv"),
}


def headers(table):
    if not isinstance(table, str) or table not in TABLES:
        raise HTTPException(422, "未知标注类型")
    with (ASSETS / TABLES[table][1]).open(encoding="utf-8-sig") as handle:
        return next(csv.reader(handle))


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, settings):
        self.settings = settings
        self.file_lock = threading.RLock()
        settings.prepare()
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL, stored_name TEXT UNIQUE NOT NULL,
                    sha256 TEXT UNIQUE NOT NULL, size_bytes INTEGER NOT NULL,
                    duration_seconds REAL NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
                    codec TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                    review_state TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL,
                    profile_json TEXT NOT NULL DEFAULT '{}', profile_revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    table_name TEXT NOT NULL, row_json TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'current', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS annotation_video ON annotations(video_id,table_name);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, video_id INTEGER, action TEXT NOT NULL,
                    detail_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '',
                    result_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS local_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS extractions(
                    id TEXT PRIMARY KEY, video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL, fps REAL NOT NULL, status TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
                    manifest_json TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS upload_sessions(
                    id TEXT PRIMARY KEY,filename TEXT NOT NULL,sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL,
                    offset INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'uploading',
                    video_id INTEGER REFERENCES videos(id) ON DELETE SET NULL,
                    auto_analyze INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL,updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS upload_identity ON upload_sessions(sha256,size_bytes,status);
                CREATE TABLE IF NOT EXISTS upload_analysis(
                    video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
                    state TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '',job_id TEXT,updated_at REAL NOT NULL,
                    origin TEXT NOT NULL DEFAULT 'upload'
                );
                CREATE TABLE IF NOT EXISTS crop_records(
                    video_id INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL,record_json TEXT NOT NULL,updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS case_exports(id TEXT PRIMARY KEY,created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS export_cases(
                    export_id TEXT NOT NULL REFERENCES case_exports(id) ON DELETE CASCADE,
                    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                    PRIMARY KEY(export_id,video_id)
                );
            """)
            conn.execute("INSERT OR IGNORE INTO local_meta VALUES('instance_id',?)", (uuid4().hex,))
            conn.execute("INSERT OR IGNORE INTO local_meta VALUES('auto_analyze_uploads','true')")
            for field,definition in (('base_revision','INTEGER'),('origin',"TEXT NOT NULL DEFAULT 'manual'"),('model_identity_json','TEXT')):
                if field not in {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}:
                    conn.execute('ALTER TABLE jobs ADD COLUMN '+field+' '+definition)
            if 'origin' not in {row[1] for row in conn.execute('PRAGMA table_info(upload_analysis)')}:
                conn.execute("ALTER TABLE upload_analysis ADD COLUMN origin TEXT NOT NULL DEFAULT 'upload'")

    @contextmanager
    def connect(self, write=False):
        conn = sqlite3.connect(self.settings.database, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def video(self, conn, video_id=None, case_id=None, clinical_video_id=None):
        if video_id is not None:
            row = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM videos WHERE case_id=?", (case_id,)).fetchone()
        if not row or clinical_video_id is not None and str(row["id"]) != str(clinical_video_id):
            raise HTTPException(404, "视频不存在")
        return row

    def public_video(self, conn, row):
        count = conn.execute("SELECT count(*) FROM annotations WHERE video_id=? AND table_name='phase'", (row["id"],)).fetchone()[0]
        extraction = conn.execute("SELECT id,revision,status,progress,error,fps,json_array_length(manifest_json,'$.frames') AS frame_count FROM extractions WHERE video_id=? ORDER BY created_at DESC LIMIT 1", (row['id'],)).fetchone()
        extraction = dict(extraction) if extraction else None
        if extraction and extraction['status'] == 'succeeded' and extraction['revision'] != row['revision']:
            extraction['status'] = 'stale'
        analysis = conn.execute('SELECT id,status,progress,error,origin FROM jobs WHERE video_id=? ORDER BY created_at DESC LIMIT 1',(row['id'],)).fetchone()
        automatic = conn.execute('SELECT state,detail,job_id,origin FROM upload_analysis WHERE video_id=?',(row['id'],)).fetchone()
        return {key: row[key] for key in ("id", "case_id", "sha256", "size_bytes", "duration_seconds", "width", "height", "codec", "revision", "review_state", "created_at")} | {
            "original_filename": row["filename"], "clinical_video_id": str(row["id"]),
            "managed": True, "locked_legacy": False, "phase_count": count,
            "media_url": f"/api/videos/{row['id']}/media",
            "editor_url": f"/clinical/editor/?workflow_video_id={row['id']}",
            "extraction": extraction,
            "analysis": dict(analysis) if analysis else None,
            "upload_analysis": dict(automatic) if automatic else None,
        }

    def rows(self, conn, video_id, table):
        headers(table)
        result = [{"index": row["id"], "table": table, "row": json.loads(row["row_json"]),
                   "source": row["source"], "created_by": "local", "created_at": row["created_at"], "updated_at": row["updated_at"]}
                  for row in conn.execute("SELECT * FROM annotations WHERE video_id=? AND table_name=?", (video_id, table))]
        return sorted(result, key=lambda item: (float(item["row"].get("start_sec") or 0), float(item["row"].get("end_sec") or 0), item["index"]))

    def compare(self, actual, expected):
        def identity(items):
            if not isinstance(items, list):
                raise HTTPException(422, "需要完整的标注版本快照")
            try:
                if any(not isinstance(item, dict) or type(item.get("index")) is not int or not isinstance(item.get("row"), dict) for item in items):
                    raise ValueError("invalid snapshot")
                return sorted((item["index"], encode(item["row"])) for item in items)
            except (KeyError, TypeError, ValueError):
                raise HTTPException(422, "标注快照格式无效") from None
        if identity(actual) != identity(expected):
            raise HTTPException(409, "标注已被其他页面更新，当前输入已保留，请刷新核对。")

    def validate(self, video, table, raw):
        if not isinstance(raw, dict):
            raise HTTPException(422, "标注必须为对象")
        row = {key: "" if raw.get(key) is None else str(raw.get(key, "")) for key in headers(table)}
        if row["case_id"] != video["case_id"] or table != "case" and row["video_id"] != str(video["id"]):
            raise HTTPException(422, "标注所属病例或视频不匹配")
        if any(len(value) > 10000 for value in row.values()):
            raise HTTPException(422, "标注字段过长")
        if table == "case":
            return row
        try:
            start, end = float(row["start_sec"]), float(row["end_sec"])
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= video["duration_seconds"]):
                raise ValueError()
        except (TypeError, ValueError):
            raise HTTPException(422, "片段起止时间必须位于视频时长内，结束时间须大于开始时间。") from None
        row["annotation_schema_version"] = "2"
        if table == "phase":
            if row["phase_label"] not in {"unused", "effective_observation"}:
                raise HTTPException(422, "阶段仅支持无用片段或有效观察")
            if row["phase_label"] == "effective_observation":
                if row["quality_level"] not in {"1", "2"} or row["visible_side"] not in {"left", "right", "unknown"}:
                    raise HTTPException(422, "请填写有效的画面质量和侧别")
            else:
                row["quality_level"], row["visible_side"] = "", "unknown"
            row["segment_id"] = row["segment_id"] or "seg_" + uuid4().hex
        return row

    def insert(self, conn, video_id, table, row, source="current"):
        timestamp = now()
        cursor = conn.execute("INSERT INTO annotations(video_id,table_name,row_json,source,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                              (video_id, table, encode(row), source, timestamp, timestamp))
        return next(item for item in self.rows(conn, video_id, table) if item["index"] == cursor.lastrowid)

    def changed(self, conn, video_id, action, detail):
        conn.execute("UPDATE videos SET revision=revision+1, review_state='pending' WHERE id=?", (video_id,))
        conn.execute("INSERT INTO audit(video_id,action,detail_json,created_at) VALUES(?,?,?,?)", (video_id, action, encode(detail), now()))

    def validate_finding_ref(self, conn, video, row, ref=None):
        phases = self.rows(conn, video["id"], "phase")
        phase = next((item for item in phases if item["row"]["segment_id"] == row["phase_segment_id"]), None)
        if not phase or phase["row"]["phase_label"] != "effective_observation":
            raise HTTPException(409, "征象需要关联当前有效观察阶段，请重新选择片段。")
        if ref is not None and (ref.get("row_id") != phase["index"] or ref.get("expected_row") != phase["row"]):
            raise HTTPException(409, "关联阶段已更新，请重新选择片段。")
        if any(row[key] != phase["row"][key] for key in ("start_sec", "end_sec")):
            raise HTTPException(422, "征象时间须与关联阶段一致")
        row["clip_quality"] = phase["row"]["quality_level"]

    def write_row(self, table, body, row_id=None):
        headers(table)
        raw = body.get("row", {})
        if not isinstance(raw, dict):
            raise HTTPException(422, "标注必须为对象")
        with self.connect(write=True) as conn:
            video = self.video(conn, case_id=raw.get("case_id"), clinical_video_id=raw.get("video_id") if table != "case" else None)
            row = self.validate(video, table, raw)
            before = self.rows(conn, video["id"], table)
            self.compare(before, body.get("expected_rows"))
            old = next((item for item in before if item["index"] == row_id), None)
            if row_id is not None and not old:
                raise HTTPException(404, "标注已被删除或更新")
            if table == "case" and row_id is None and before:
                raise HTTPException(409, "病例信息已存在，请刷新后编辑")
            if table == "finding":
                self.validate_finding_ref(conn, video, row, body.get("phase_ref"))
            if row_id is not None:
                conn.execute("DELETE FROM annotations WHERE id=?", (row_id,))
            if table == "phase":
                start, end = float(row["start_sec"]), float(row["end_sec"])
                for item in self.rows(conn, video["id"], table):
                    previous = item["row"]
                    left, right = float(previous["start_sec"]), float(previous["end_sec"])
                    if right <= start or left >= end:
                        continue
                    conn.execute("DELETE FROM annotations WHERE id=?", (item["index"],))
                    for a, b in ((left, min(start, right)), (max(end, left), right)):
                        if a < b:
                            split = {**previous, "start_sec": str(a), "end_sec": str(b), "segment_id": "seg_" + uuid4().hex}
                            self.insert(conn, video["id"], table, split, item["source"])
                if any(item["row"]["segment_id"] == row["segment_id"] for item in self.rows(conn, video["id"], table)):
                    raise HTTPException(409, "阶段编号重复")
            created = self.insert(conn, video["id"], table, row)
            self.changed(conn, video["id"], "save:" + table, {"before": before, "after": self.rows(conn, video["id"], table)})
            return {"ok": True, "updated" if row_id is not None else "created": created}

    def restore(self, body):
        table = body.get("table")
        if not isinstance(table, str) or table not in {"phase", "finding", "segment"}:
            raise HTTPException(422, "无法恢复此标注类型")
        tables = ("phase", "finding") if table == "segment" else (table,)
        if any(t not in {"phase", "finding"} for t in tables):
            raise HTTPException(422, "无法恢复此标注类型")
        expected, restore = body.get("expected_rows"), body.get("restore_rows")
        if table != "segment":
            expected, restore = {table: expected}, {table: restore}
        if not isinstance(expected, dict) or not isinstance(restore, dict):
            raise HTTPException(422, "恢复快照格式无效")
        with self.connect(write=True) as conn:
            video = self.video(conn, case_id=body.get("case_id"), clinical_video_id=body.get("video_id"))
            before, validated = {}, {}
            for name in tables:
                before[name] = self.rows(conn, video["id"], name)
                self.compare(before[name], expected.get(name))
                items = restore.get(name)
                if not isinstance(items, list) or len(items) > 10000:
                    raise HTTPException(422, "恢复记录数量或格式无效")
                validated[name] = [self.validate(video, name, item.get("row")) for item in items if isinstance(item, dict)]
                if len(validated[name]) != len(items):
                    raise HTTPException(422, "恢复记录格式无效")
                if name == "phase":
                    ordered = sorted(validated[name], key=lambda row: float(row["start_sec"]))
                    if len({r["segment_id"] for r in ordered}) != len(ordered) or any(float(a["end_sec"]) > float(b["start_sec"]) for a, b in zip(ordered, ordered[1:])):
                        raise HTTPException(422, "恢复阶段存在重叠或重复编号")
            for name in tables:
                conn.execute("DELETE FROM annotations WHERE video_id=? AND table_name=?", (video["id"], name))
                for row in validated[name]:
                    self.insert(conn, video["id"], name, row)
            self.changed(conn, video["id"], "history:" + table, before)
            result = {name: self.rows(conn, video["id"], name) for name in tables}
            return {"ok": True, "rows": result if table == "segment" else result[table]}
