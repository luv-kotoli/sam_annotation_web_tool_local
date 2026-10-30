"""Local-only replacement for the gateway and storage APIs used by the editor."""
from contextlib import asynccontextmanager
import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit
from uuid import uuid4
import zipfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .media import probe
from .settings import Settings
from .store import ASSETS, TABLES, Store, encode, headers, now
from .uploads import VideoUploads
from .deletion import CaseDeletion


def create_app(settings=None):
    settings = settings or Settings.from_env()
    store = Store(settings)
    uploads = VideoUploads(store)
    deletion = CaseDeletion(store)

    @asynccontextmanager
    async def lifespan(app):
        from .inference import AnalysisJobs
        from .extraction import Extractions
        jobs = AnalysisJobs(store)
        app.state.analysis = jobs
        app.state.extractions = Extractions(store)
        yield
        app.state.extractions.close()
        jobs.close()

    app = FastAPI(title="本地视频阶段标注", lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings
    uploads.install(app)
    app.mount("/clinical/assets", StaticFiles(directory=ASSETS), name="editor-assets")
    app.mount("/workflow/static", StaticFiles(directory=ASSETS), name="workspace-assets")
    app.mount("/static", StaticFiles(directory=ASSETS), name="local-assets")

    @app.middleware("http")
    async def same_origin_writes(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "请从本工具页面执行操作"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/", response_class=HTMLResponse)
    @app.get("/clinical/", response_class=HTMLResponse)
    @app.get("/app/", response_class=HTMLResponse)
    def index():
        return (ASSETS / "index.html").read_text()

    @app.get("/clinical/editor/", response_class=HTMLResponse)
    def editor():
        return (ASSETS / "workspace.html").read_text().replace("{{mode}}", "phase")

    @app.get("/health")
    def health():
        from . import __version__
        return {"ok": True, "mode": "local", "version": __version__}

    @app.get("/api/capabilities")
    def capabilities():
        from .inference import availability
        return {"ok": True, "ffprobe": bool(shutil.which("ffprobe")), "analysis": availability(settings), "max_upload_bytes": settings.max_upload_bytes,
                "storage": {"data_dir": str(settings.data_dir), "video_dir": str(settings.videos)}}

    @app.get("/annotation/api/me")
    def me():
        return {"ok": True, "user": {"username": "local", "role": "admin"}, "features": {}}

    @app.get("/clinical/api/schema")
    def schema():
        return {"ok": True, "annotationDate": date.today().isoformat(), "exportUrl": "/clinical/export.zip",
                "catalog": json.loads((ASSETS / "catalog.json").read_text()),
                "tables": {table: {"title": title, "filename": filename, "header": headers(table)} for table, (title, filename) in TABLES.items()}}

    @app.get("/api/videos")
    def list_videos(q: str = "", offset: int = 0, limit: int = 50):
        offset, limit = max(0, offset), max(1, min(100, limit))
        with store.connect() as conn:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where = "WHERE filename LIKE ? ESCAPE '\\' OR case_id LIKE ? ESCAPE '\\'"
            total = conn.execute("SELECT count(*) FROM videos " + where, (pattern, pattern)).fetchone()[0]
            rows = conn.execute("SELECT * FROM videos " + where + " ORDER BY id DESC LIMIT ? OFFSET ?", (pattern, pattern, limit, offset)).fetchall()
            return {"ok": True, "total": total, "videos": [store.public_video(conn, row) for row in rows]}

    @app.post("/api/videos/upload", status_code=201)
    async def upload(request: Request, filename: str):
        filename = uploads.filename(filename)
        suffix = Path(filename).suffix.lower()
        temporary = settings.temporary / (uuid4().hex + suffix)
        digest, size = hashlib.sha256(), 0
        try:
            # Streaming keeps upload memory independent of video length.
            with temporary.open("xb") as handle:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, "视频超过本地上传大小限制")
                    digest.update(chunk)
                    await run_in_threadpool(handle.write, chunk)
            if size == 0:
                raise HTTPException(422, "视频文件为空")
            sha256 = digest.hexdigest()
            with store.connect() as conn:
                duplicate = conn.execute("SELECT * FROM videos WHERE sha256=?", (sha256,)).fetchone()
                if duplicate:
                    return JSONResponse({"detail": "此视频已在本机视频库中，改名不会重复导入。", "existing_video": store.public_video(conn, duplicate)}, status_code=409)
            metadata = await run_in_threadpool(probe, temporary)
            try:
                return await run_in_threadpool(uploads.register, temporary, filename, sha256, size, metadata)
            except HTTPException as error:
                if error.status_code == 409 and isinstance(error.detail, dict):
                    return JSONResponse({'detail': error.detail['message'], 'existing_video': error.detail['existing_video']}, status_code=409)
                raise
        finally:
            temporary.unlink(missing_ok=True)

    @app.api_route("/api/videos/{video_id}/media", methods=["GET", "HEAD"])
    def media(video_id: int):
        with store.connect() as conn:
            video = store.video(conn, video_id)
            path = settings.videos / video["stored_name"]
            if not path.is_file():
                raise HTTPException(404, "本机视频文件缺失，请检查数据目录")
            return FileResponse(path, filename=video["filename"], content_disposition_type="inline")

    @app.delete("/api/videos/{video_id}")
    def delete_video(video_id: int, body: dict):
        with store.connect() as conn:
            video = store.video(conn, video_id)
            if body.get('expected_revision') != video['revision'] or body.get('confirm_filename') != video['filename']:
                raise HTTPException(409, '视频已更新或确认信息不符，请刷新后重试')
            preview = deletion.plan(conn, [video_id])[0]
        return deletion.delete({'video_ids': [video_id], 'token': preview['token'], 'confirm_count': 1})

    @app.post('/api/cases/delete-preview')
    def delete_preview(body: dict):
        return deletion.preview(body)

    @app.post('/api/cases/delete')
    def delete_cases(body: dict):
        return deletion.delete(body)

    @app.get('/api/settings')
    def get_settings():
        with store.connect() as conn:
            return {'ok': True, 'auto_analyze_uploads': uploads.automatic_enabled(conn)}

    @app.patch('/api/settings')
    def put_settings(body: dict):
        enabled = body.get('auto_analyze_uploads')
        if type(enabled) is not bool or set(body) != {'auto_analyze_uploads'}:
            raise HTTPException(422, '自动分析设置无效')
        with store.connect(write=True) as conn:
            conn.execute("UPDATE local_meta SET value=? WHERE key='auto_analyze_uploads'", ('true' if enabled else 'false',))
        return {'ok': True, 'auto_analyze_uploads': enabled}

    def profile(video):
        return {"ok": True, "case_id": video["case_id"], "patient_name": "", "details": json.loads(video["profile_json"]), "revision": video["profile_revision"], "locked": False}

    def context(conn, video):
        return {"remote_video": store.public_video(conn, video), "case_exists": True, "locked_legacy": False}

    @app.get("/clinical/api/videos/{video_id}/editor-context")
    def editor_context(video_id: int):
        with store.connect() as conn:
            video = store.video(conn, video_id)
            ids = [r[0] for r in conn.execute("SELECT id FROM videos ORDER BY id")]
            index = ids.index(video_id)
            return {"ok": True, "video": store.public_video(conn, video), "context": context(conn, video), "profile": profile(video),
                    "rows": {table: store.rows(conn, video_id, table) for table in TABLES},
                    "navigation": {"position": index + 1, "total": len(ids), "previous": {"id": ids[index - 1]} if index else None,
                                   "next": {"id": ids[index + 1]} if index + 1 < len(ids) else None}}

    @app.get("/clinical/api/videos/context")
    def video_context(case_id: str, video_id: str):
        with store.connect() as conn:
            return context(conn, store.video(conn, case_id=case_id, clinical_video_id=video_id))

    @app.get("/clinical/api/case-profile")
    def get_profile(case_id: str):
        with store.connect() as conn:
            return profile(store.video(conn, case_id=case_id))

    @app.put("/clinical/api/case-profile")
    def put_profile(case_id: str, body: dict):
        with store.connect(write=True) as conn:
            video = store.video(conn, case_id=case_id)
            if body.get("expected_revision") != video["profile_revision"]:
                raise HTTPException(409, "病例信息已更新，请刷新后重试")
            details = body.get("details")
            if not isinstance(details, dict) or set(details) - {"sex", "age"}:
                raise HTTPException(422, "病例信息格式无效")
            sex, age = str(details.get("sex", "")), str(details.get("age", ""))
            if sex not in {"", "male", "female"} or age and (not age.isdigit() or not 0 <= int(age) <= 130):
                raise HTTPException(422, "性别或年龄无效")
            conn.execute("UPDATE videos SET profile_json=?,profile_revision=profile_revision+1 WHERE id=?", (encode({"sex": sex, "age": age}), video["id"]))
            return profile(store.video(conn, video["id"]))

    @app.get("/clinical/api/rows")
    def list_rows(table: str, case_id: str, video_id: str = ""):
        headers(table)
        with store.connect() as conn:
            video = store.video(conn, case_id=case_id, clinical_video_id=video_id if table != "case" else None)
            return {"ok": True, "table": table, "header": headers(table), "rows": store.rows(conn, video["id"], table)}

    @app.post("/clinical/api/rows")
    def create_row(body: dict):
        return store.write_row(body.get("table"), body)

    @app.put("/clinical/api/rows/{table}/{row_id}")
    def update_row(table: str, row_id: int, body: dict):
        return store.write_row(table, body, row_id)

    @app.delete("/clinical/api/rows/{table}/{row_id}")
    def delete_row(table: str, row_id: int, body: dict):
        with store.connect(write=True) as conn:
            item = conn.execute("SELECT * FROM annotations WHERE id=? AND table_name=?", (row_id, table)).fetchone()
            if not item:
                raise HTTPException(404, "标注不存在")
            before = store.rows(conn, item["video_id"], table)
            store.compare(before, body.get("expected_rows"))
            conn.execute("DELETE FROM annotations WHERE id=?", (row_id,))
            store.changed(conn, item["video_id"], "delete:" + table, before)
        return {"ok": True}

    @app.post("/clinical/api/annotation-history")
    def restore(body: dict):
        return store.restore(body)

    @app.post("/annotation/api/workflow/videos/{video_id}/review")
    def review(video_id: int, body: dict):
        with store.connect(write=True) as conn:
            video = store.video(conn, video_id)
            if body.get("expected_revision") != video["revision"]:
                raise HTTPException(409, "视频已更新，请刷新后复核")
            store.compare(store.rows(conn, video_id, "phase"), body.get("expected_rows"))
            unchanged = video["review_state"] == "reviewed"
            conn.execute("UPDATE videos SET review_state='reviewed' WHERE id=?", (video_id,))
            return {"ok": True, "video": store.public_video(conn, store.video(conn, video_id)), "job_id": None, "unchanged": unchanged}

    @app.get("/annotation/api/workflow/videos/{video_id}/crop-record")
    def crop_record(video_id: int):
        with store.connect() as conn:
            store.video(conn, video_id)
            record = conn.execute('SELECT record_json FROM crop_records WHERE video_id=?', (video_id,)).fetchone()
            if record:
                return {'state': 'stored', 'record': json.loads(record[0])}
            active = conn.execute("SELECT 1 FROM jobs WHERE video_id=? AND status IN ('queued','running')", (video_id,)).fetchone()
        return {'state': 'pending' if active else 'unavailable', 'record': None}

    def export_payload(conn, video):
        return {"format": "local-video-annotations.v1", "exported_at": now(), "video": store.public_video(conn, video),
                "profile": profile(video), "rows": {table: store.rows(conn, video["id"], table) for table in TABLES}}

    @app.get("/api/videos/{video_id}/annotations.json")
    def export_json(video_id: int):
        with store.connect() as conn:
            video = store.video(conn, video_id)
            return Response(json.dumps(export_payload(conn, video), ensure_ascii=False, indent=2), media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="video-{video_id}-annotations.json"'})

    @app.get("/clinical/export.zip")
    @app.get("/api/videos/{video_id}/export.zip")
    def export_zip(video_id: int | None = None):
        buffer = io.BytesIO()
        with store.connect() as conn:
            videos = [store.video(conn, video_id)] if video_id is not None else conn.execute("SELECT * FROM videos ORDER BY id").fetchall()
            payloads = [export_payload(conn, video) for video in videos]
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("annotations.json", json.dumps({"format": "local-video-archive.v1", "videos": payloads}, ensure_ascii=False, indent=2))
            for table, (_, filename) in TABLES.items():
                output = io.StringIO(newline="")
                writer = csv.DictWriter(output, fieldnames=headers(table))
                writer.writeheader()
                for payload in payloads:
                    writer.writerows(item["row"] for item in payload["rows"][table])
                archive.writestr(filename, output.getvalue().encode("utf-8-sig"))
        return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="video-annotations.zip"'})

    @app.get("/api/jobs")
    def list_jobs():
        return app.state.analysis.list()

    @app.post("/api/videos/{video_id}/analyze", status_code=202)
    def analyze(video_id: int):
        return app.state.analysis.submit(video_id)

    @app.post('/api/analysis-batches', status_code=202)
    def analyze_batch(body: dict):
        return app.state.analysis.batch(body)

    @app.post("/api/jobs/{job_id}/apply")
    def apply_analysis(job_id: str, body: dict):
        return app.state.analysis.apply(job_id, body)

    @app.get("/api/jobs/{job_id}/result")
    def analysis_result(job_id: str):
        return JSONResponse(app.state.analysis.result(job_id), headers={"Content-Disposition": f'attachment; filename="analysis-{job_id}.json"'})

    @app.post('/api/videos/{video_id}/extract',status_code=202)
    def extract(video_id:int,body:dict):
        return app.state.extractions.submit(video_id,body)

    @app.post('/api/exports')
    def export_cases(body:dict):
        return app.state.extractions.export(body)

    @app.get('/api/exports/{identifier}')
    def download_cases(identifier:str):
        import re
        if not re.fullmatch('[a-f0-9]{32}',identifier):
            raise HTTPException(404,'导出包不存在')
        path=settings.exports/(identifier+'.zip')
        if not path.is_file():
            raise HTTPException(404,'导出包不存在')
        return FileResponse(path,media_type='application/zip',filename='local-cases-'+identifier[:8]+'.zip')

    return app
