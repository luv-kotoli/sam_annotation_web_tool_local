"""Crash-resumable local uploads, verified chunks and atomic video registration."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import threading
import time
from uuid import uuid4

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .media import EXTENSIONS, probe

CHUNK_BYTES = 8 * 1024**2
SESSION_TTL = 7 * 86400


class VideoUploads:
    def __init__(self, store):
        self.store = store
        self.locks = [threading.RLock() for _ in range(32)]
        with store.connect(write=True) as conn:
            self.expire(conn)

    def expire(self, conn):
        expired = conn.execute("SELECT id FROM upload_sessions WHERE status='uploading' AND updated_at<?", (time.time()-SESSION_TTL,)).fetchall()
        for row in expired:
            (self.store.settings.uploads / (row['id'] + '.part')).unlink(missing_ok=True)
            conn.execute("UPDATE upload_sessions SET status='discarded',filename='',sha256='',size_bytes=0,offset=0 WHERE id=?", (row['id'],))

    def lock(self, identifier):
        if not re.fullmatch('[a-f0-9]{32}', identifier):
            raise HTTPException(404, '上传会话不存在')
        return self.locks[int(identifier[:8], 16) % len(self.locks)]

    @staticmethod
    def filename(value):
        if not isinstance(value, str):
            raise HTTPException(422, '请提供视频文件名')
        value = value.replace('\\', '/').split('/')[-1].strip()
        if Path(value).suffix.lower() not in EXTENSIONS or not value or len(value) > 240 or any(ord(c) < 32 for c in value):
            raise HTTPException(422, '请选择 mp4、mov、m4v、webm、mkv 或 avi 视频')
        return value

    def automatic_enabled(self, conn):
        return conn.execute("SELECT value FROM local_meta WHERE key='auto_analyze_uploads'").fetchone()[0] == 'true'

    def duplicate(self, conn, digest):
        old = conn.execute('SELECT * FROM videos WHERE sha256=?', (digest,)).fetchone()
        if old:
            raise HTTPException(409, {'message': '此视频已在本机视频库中，改名不会重复导入。',
                                      'existing_video': self.store.public_video(conn, old)})

    def register(self, temporary, filename, digest, size, metadata, session_id=None, auto_analyze=None):
        settings = self.store.settings
        target = settings.videos / ((session_id or uuid4().hex) + Path(filename).suffix.lower())
        moved = False
        try:
            with self.store.connect(write=True) as conn:
                self.duplicate(conn, digest)
                # Keep resumable bytes until the DB commit. A process failure at
                # finalization can retry without re-uploading the entire video.
                if session_id:
                    try:
                        os.link(temporary, target)
                    except OSError:
                        shutil.copyfile(temporary, target)
                        with target.open('rb') as handle:
                            os.fsync(handle.fileno())
                else:
                    os.replace(temporary, target)
                moved = True
                case_id = 'LOCAL-' + uuid4().hex[:12].upper()
                cursor = conn.execute('''INSERT INTO videos(case_id,filename,stored_name,sha256,size_bytes,duration_seconds,width,height,codec,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''', (case_id, filename, target.name, digest, size, metadata['duration_seconds'], metadata['width'], metadata['height'], metadata['codec'], time.time()))
                video_id = cursor.lastrowid
                enabled = self.automatic_enabled(conn) if auto_analyze is None else auto_analyze
                conn.execute('INSERT INTO upload_analysis(video_id,state,updated_at) VALUES(?,?,?)',
                             (video_id, 'pending' if enabled else 'disabled', time.time()))
                if session_id:
                    conn.execute("UPDATE upload_sessions SET status='succeeded',offset=?,video_id=?,updated_at=? WHERE id=?",
                                 (size, video_id, time.time(), session_id))
                result = {'ok': True, 'video': self.store.public_video(conn, self.store.video(conn, video_id))}
            if session_id:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # A committed upload must never roll back on cleanup.
            return result
        except BaseException:
            if moved:
                if session_id:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(target, temporary)
            raise

    def session(self, conn, identifier):
        self.lock(identifier)
        row = conn.execute('SELECT * FROM upload_sessions WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, '上传会话不存在')
        if row['status'] in {'deleted', 'discarded'}:
            raise HTTPException(410, '上传会话已删除，请重新选择文件')
        if row['status'] == 'uploading' and row['updated_at'] < time.time() - SESSION_TTL:
            raise HTTPException(410, '上传会话已过期，请重新选择文件')
        return row

    def reply(self, conn, row):
        result = None
        if row['status'] == 'succeeded':
            if row['video_id'] is None:
                raise HTTPException(410, '此上传对应的病例已删除')
            result = {'ok': True, 'video': self.store.public_video(conn, self.store.video(conn, row['video_id']))}
        return {'ok': True, 'session_id': row['id'], 'offset': row['offset'], 'size_bytes': row['size_bytes'],
                'status': row['status'], 'chunk_size': CHUNK_BYTES, 'result': result}

    def start(self, body):
        filename = self.filename(body.get('filename'))
        digest, size = body.get('sha256'), body.get('size_bytes')
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest) or type(size) is not int or not 0 < size <= self.store.settings.max_upload_bytes:
            raise HTTPException(422, '视频摘要或大小无效')
        with self.store.connect(write=True) as conn:
            self.expire(conn)
            prior = conn.execute("SELECT * FROM upload_sessions WHERE sha256=? AND size_bytes=? AND status='succeeded' AND video_id IS NOT NULL ORDER BY updated_at DESC LIMIT 1", (digest, size)).fetchone()
            if prior:
                return self.reply(conn, prior)
            self.duplicate(conn, digest)
            prior = conn.execute("SELECT * FROM upload_sessions WHERE sha256=? AND size_bytes=? AND status='uploading' AND updated_at>? ORDER BY updated_at DESC LIMIT 1", (digest, size, time.time()-SESSION_TTL)).fetchone()
            if prior:
                return self.reply(conn, prior)
            if conn.execute("SELECT count(*) FROM upload_sessions WHERE status='uploading' AND updated_at>?", (time.time()-SESSION_TTL,)).fetchone()[0] >= 32:
                raise HTTPException(429, '未完成上传过多，请完成或丢弃旧上传后再试')
            identifier = uuid4().hex
            path = self.store.settings.uploads / (identifier + '.part')
            try:
                path.touch(exist_ok=False)
                conn.execute('INSERT INTO upload_sessions(id,filename,sha256,size_bytes,auto_analyze,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                             (identifier, filename, digest, size, int(self.automatic_enabled(conn)), time.time(), time.time()))
                return self.reply(conn, self.session(conn, identifier))
            except BaseException:
                path.unlink(missing_ok=True)
                raise

    def status(self, identifier):
        with self.lock(identifier), self.store.connect() as conn:
            return self.reply(conn, self.session(conn, identifier))

    def append(self, identifier, offset, digest, data):
        if not re.fullmatch('[a-f0-9]{64}', digest) or not data or len(data) > CHUNK_BYTES or hashlib.sha256(data).hexdigest() != digest:
            raise HTTPException(422, '分块内容校验失败')
        with self.lock(identifier), self.store.connect(write=True) as conn:
            row = self.session(conn, identifier)
            if row['status'] != 'uploading' or offset + len(data) > row['size_bytes']:
                raise HTTPException(409, '上传状态或分块范围已变化')
            path = self.store.settings.uploads / (identifier + '.part')
            if not path.is_file() or path.stat().st_size < row['offset']:
                raise HTTPException(409, '已提交的上传分块缺失，请丢弃该会话后重新上传')
            # A lost acknowledgement can retry the same exact chunk safely.
            if offset < row['offset'] and offset + len(data) <= row['offset']:
                with path.open('rb') as handle:
                    handle.seek(offset)
                    if handle.read(len(data)) != data:
                        raise HTTPException(409, '重传分块与已保存内容不一致')
                return self.reply(conn, row)
            if offset != row['offset']:
                raise HTTPException(409, '上传偏移不匹配，请查询已接收进度后继续')
            with path.open('r+b') as handle:
                # Discard uncommitted bytes left by a crash between fsync and SQLite commit.
                handle.truncate(row['offset'])
                handle.seek(offset)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            conn.execute('UPDATE upload_sessions SET offset=?,updated_at=? WHERE id=?', (offset+len(data), time.time(), identifier))
            return self.reply(conn, self.session(conn, identifier))

    def complete(self, identifier):
        with self.lock(identifier):
            with self.store.connect() as conn:
                row = self.session(conn, identifier)
                if row['status'] == 'succeeded':
                    return self.reply(conn, row)['result']
                if row['offset'] != row['size_bytes']:
                    raise HTTPException(409, '视频尚未上传完整，请继续上传')
            path = self.store.settings.uploads / (identifier + '.part')
            digest = hashlib.sha256()
            try:
                with path.open('rb') as handle:
                    for chunk in iter(lambda: handle.read(1024**2), b''):
                        digest.update(chunk)
            except FileNotFoundError:
                raise HTTPException(409, '上传文件缺失，请重新上传') from None
            if digest.hexdigest() != row['sha256'] or path.stat().st_size != row['size_bytes']:
                raise HTTPException(422, '完整视频 SHA-256 校验失败，请丢弃该会话后重新上传')
            metadata = probe(path)
            return self.register(path, row['filename'], row['sha256'], row['size_bytes'], metadata, identifier, bool(row['auto_analyze']))

    def discard(self, identifier):
        with self.lock(identifier), self.store.connect(write=True) as conn:
            row = self.session(conn, identifier)
            if row['status'] == 'succeeded':
                raise HTTPException(409, '已完成上传不能作为未完成会话丢弃，请从病例库删除')
            conn.execute("UPDATE upload_sessions SET status='discarded',updated_at=? WHERE id=?", (time.time(), identifier))
            (self.store.settings.uploads / (identifier + '.part')).unlink(missing_ok=True)
        return {'ok': True}

    def install(self, app):
        @app.post('/api/upload-sessions')
        def start(body: dict):
            return self.start(body)

        @app.get('/api/upload-sessions/{identifier}')
        def status(identifier: str):
            return self.status(identifier)

        @app.put('/api/upload-sessions/{identifier}')
        async def append(identifier: str, request: Request):
            self.lock(identifier)
            offset = request.headers.get('upload-offset', '')
            if not offset.isascii() or not offset.isdecimal() or len(offset) > 20:
                raise HTTPException(422, '上传分块偏移无效')
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > CHUNK_BYTES:
                    raise HTTPException(413, '上传分块超过 8 MiB')
            return await run_in_threadpool(self.append, identifier, int(offset), request.headers.get('x-chunk-sha256', ''), bytes(data))

        @app.post('/api/upload-sessions/{identifier}/complete')
        def complete(identifier: str):
            return self.complete(identifier)

        @app.delete('/api/upload-sessions/{identifier}')
        def discard(identifier: str):
            return self.discard(identifier)
