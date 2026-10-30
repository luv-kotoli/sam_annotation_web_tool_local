"""Case-wide deletion with preview, version fencing and recoverable file moves."""
import hashlib
import json
import os
import re
import shutil
import zipfile
from uuid import uuid4

from fastapi import HTTPException

from .store import encode, now


class CaseDeletion:
    def __init__(self, store):
        self.store = store
        self.recover()
        self.index_existing_exports()

    def index_existing_exports(self):
        # Older releases created valid bundles without a DB index. Register
        # those caches too so deleting a case removes copies of its records.
        with self.store.connect(write=True) as conn:
            for path in self.store.settings.exports.glob('*.zip'):
                if not re.fullmatch('[a-f0-9]{32}', path.stem) or conn.execute('SELECT 1 FROM case_exports WHERE id=?', (path.stem,)).fetchone():
                    continue
                try:
                    with zipfile.ZipFile(path) as archive:
                        if archive.getinfo('manifest.json').file_size > 16*1024**2:
                            continue
                        manifest = json.loads(archive.read('manifest.json'))
                    ids = []
                    for case in manifest['cases']:
                        row = conn.execute('SELECT id FROM videos WHERE case_id=? AND sha256=?', (case['case_id'], case['video']['sha256'])).fetchone()
                        if row:
                            ids.append(row[0])
                    if ids:
                        conn.execute('INSERT INTO case_exports VALUES(?,?)', (path.stem, path.stat().st_mtime))
                        conn.executemany('INSERT OR IGNORE INTO export_cases VALUES(?,?)', [(path.stem, identifier) for identifier in ids])
                except (ValueError, TypeError, KeyError, OSError, zipfile.BadZipFile):
                    continue

    def recover(self):
        # A crash before the SQLite commit restores every parked file. After
        # commit, the absent video IDs prove the quarantine may be removed.
        for directory in self.store.settings.temporary.glob('delete-*'):
            manifest = directory / 'journal.json'
            if not manifest.is_file():
                continue
            journal = json.loads(manifest.read_text())
            with self.store.connect() as conn:
                present = any(conn.execute('SELECT 1 FROM videos WHERE id=?', (identifier,)).fetchone() for identifier in journal['ids'])
            if present:
                self.restore(directory, journal['paths'])
            shutil.rmtree(directory)

    def restore(self, directory, paths):
        for index, relative in reversed(list(enumerate(paths))):
            parked = directory / str(index)
            if parked.exists():
                target = self.store.settings.data_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(parked, target)

    @staticmethod
    def ids(body):
        ids = body.get('video_ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(type(item) is not int for item in ids) or len(set(ids)) != len(ids):
            raise HTTPException(422, '请选择 1–100 个不同病例')
        return sorted(ids)

    def plan(self, conn, ids):
        cases, paths, archives, identities = [], [], set(), []
        for identifier in ids:
            video = self.store.video(conn, identifier)
            jobs = [dict(row) for row in conn.execute('SELECT id,status,updated_at FROM jobs WHERE video_id=? ORDER BY id', (identifier,))]
            extractions = [dict(row) for row in conn.execute('SELECT id,status,updated_at,manifest_json FROM extractions WHERE video_id=? ORDER BY id', (identifier,))]
            if any(item['status'] in {'queued', 'running'} for item in jobs + extractions):
                raise HTTPException(409, f"{video['filename']} 仍有分析或抽帧任务，请等待结束后再删除")
            annotations = [dict(row) for row in conn.execute('SELECT * FROM annotations WHERE video_id=? ORDER BY id', (identifier,))]
            exports = [row[0] for row in conn.execute('SELECT export_id FROM export_cases WHERE video_id=? ORDER BY export_id', (identifier,))]
            identities.append({'video': dict(video), 'jobs': jobs, 'extractions': extractions, 'annotations': annotations, 'exports': exports})
            paths.append(self.store.settings.videos / video['stored_name'])
            paths.extend(self.store.settings.uploads / (row[0]+'.part') for row in conn.execute('SELECT id FROM upload_sessions WHERE video_id=?', (identifier,)))
            paths.extend(self.store.settings.frames / item['id'] for item in extractions)
            archives.update(exports)
            cases.append({'id': identifier, 'filename': video['filename'], 'annotation_count': len(annotations),
                          'frame_count': sum(len(json.loads(item['manifest_json']).get('frames', [])) for item in extractions if item['manifest_json'])})
        paths.extend(self.store.settings.exports / (identifier + '.zip') for identifier in sorted(archives))
        token = hashlib.sha256(encode(identities).encode()).hexdigest()
        return {'ok': True, 'token': token, 'case_count': len(cases), 'cases': cases,
                'annotation_count': sum(case['annotation_count'] for case in cases),
                'frame_count': sum(case['frame_count'] for case in cases), 'cached_export_count': len(archives)}, paths, archives

    def preview(self, body):
        with self.store.connect() as conn:
            conn.execute('BEGIN')
            return self.plan(conn, self.ids(body))[0]

    def delete(self, body):
        ids = self.ids(body)
        directory = self.store.settings.temporary / ('delete-' + uuid4().hex)
        paths = []
        committed = False
        with self.store.file_lock:
            try:
                with self.store.connect(write=True) as conn:
                    preview, targets, archives = self.plan(conn, ids)
                    if body.get('token') != preview['token'] or type(body.get('confirm_count')) is not int or body['confirm_count'] != len(ids):
                        raise HTTPException(409, '病例内容已更新或确认数量不符，请重新预览后删除')
                    paths = [str(path.relative_to(self.store.settings.data_dir)) for path in targets if path.exists()]
                    directory.mkdir()
                    with (directory / 'journal.json').open('w') as handle:
                        json.dump({'ids': ids, 'paths': paths}, handle)
                        handle.flush()
                        os.fsync(handle.fileno())
                    for index, relative in enumerate(paths):
                        os.replace(self.store.settings.data_dir / relative, directory / str(index))
                    for identifier in archives:
                        conn.execute('DELETE FROM case_exports WHERE id=?', (identifier,))
                    for identifier in ids:
                        conn.execute("UPDATE upload_sessions SET status='deleted',filename='',sha256='',size_bytes=0,offset=0 WHERE video_id=?", (identifier,))
                        conn.execute('DELETE FROM audit WHERE video_id=?', (identifier,))
                        conn.execute('DELETE FROM videos WHERE id=?', (identifier,))
                    conn.execute("INSERT INTO audit(action,detail_json,created_at) VALUES('delete-cases',?,?)", (encode({'ids': ids, 'count': len(ids)}), now()))
                committed = True
            finally:
                if directory.exists():
                    if not committed:
                        self.restore(directory, paths)
                    shutil.rmtree(directory)
        return {'ok': True, 'deleted_count': len(ids)}
