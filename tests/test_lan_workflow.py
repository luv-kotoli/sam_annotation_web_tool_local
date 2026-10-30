"""LAN policies use generated video and synthetic AI records, never fake models."""
import hashlib
import json
import os
from pathlib import Path
import time
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_annotation.app import create_app
from local_annotation.crops import crop_record
from local_annotation.deletion import CaseDeletion
from local_annotation.store import encode
from test_local import LocalWorkflowTests as Base


class LanWorkflowTests(Base):
    # Reuse generated-video fixtures without running the base suite twice.
    for _name in dir(Base):
        if _name.startswith('test_'):
            locals()[_name] = None

    def start_upload(self, content, name='resume.mp4', digest=None):
        response = self.client.post('/api/upload-sessions', json={'filename': name, 'size_bytes': len(content), 'sha256': digest or hashlib.sha256(content).hexdigest()})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def chunk(self, identifier, offset, data, digest=None):
        return self.client.put('/api/upload-sessions/'+identifier, content=data, headers={'Upload-Offset': str(offset), 'X-Chunk-Sha256': digest or hashlib.sha256(data).hexdigest()})

    def another(self, suffix=b'new-video'):
        response = self.client.post('/api/videos/upload?filename=second.mp4', content=self.content+suffix)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['video']

    def preview(self, ids):
        response = self.client.post('/api/cases/delete-preview', json={'video_ids': ids})
        self.assertEqual(response.status_code, 200, response.text)
        return {'video_ids': ids, 'token': response.json()['token'], 'confirm_count': len(ids)}

    def test_resume_after_restart_lost_ack_and_uncommitted_tail(self):
        content = self.content+b'resumable-video'
        session = self.start_upload(content)
        identifier = session['session_id']
        first = content[:10000]
        self.assertEqual(self.chunk(identifier, 0, first).json()['offset'], 10000)
        self.assertEqual(self.chunk(identifier, 0, first).json()['offset'], 10000)
        with (self.settings.uploads/(identifier+'.part')).open('ab') as handle:
            handle.write(b'uncommitted disk tail')
        self.client.__exit__(None, None, None)
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        resumed = self.start_upload(content, 'renamed.mp4')
        self.assertEqual((resumed['session_id'], resumed['offset']), (identifier, 10000))
        self.assertEqual(self.chunk(identifier, 10000, content[10000:]).json()['offset'], len(content))
        # Simulate a process dying after linking the verified bytes but before
        # committing registration. Both paths can reference the same inode.
        os.link(self.settings.uploads/(identifier+'.part'), self.settings.videos/(identifier+'.mp4'))
        response = self.client.post(f'/api/upload-sessions/{identifier}/complete')
        self.assertEqual(response.status_code, 200, response.text)
        video = response.json()['video']
        self.assertEqual(self.client.get(video['media_url']).content, content)
        self.assertEqual(self.client.post(f'/api/upload-sessions/{identifier}/complete').json()['video']['id'], video['id'])
        self.assertEqual(self.start_upload(content)['result']['video']['id'], video['id'])
        self.assertEqual(self.client.get('/api/videos').json()['total'], 2)
        self.assertEqual(list(self.settings.uploads.iterdir()), [])

    def test_chunk_integrity_offsets_cancel_and_full_digest(self):
        content = self.content+b'digest'
        identifier = self.start_upload(content)['session_id']
        self.assertEqual(self.chunk(identifier, 1, content[:100]).status_code, 409)
        self.assertEqual(self.chunk(identifier, 0, content[:100], '0'*64).status_code, 422)
        self.assertEqual(self.chunk(identifier, 0, content[:100]).status_code, 200)
        self.assertEqual(self.chunk(identifier, 0, b'x'*100).status_code, 409)
        self.assertEqual(self.client.post(f'/api/upload-sessions/{identifier}/complete').status_code, 409)
        self.assertEqual(self.client.delete('/api/upload-sessions/'+identifier).status_code, 200)
        self.assertEqual(self.client.get('/api/upload-sessions/'+identifier).status_code, 410)
        self.assertFalse((self.settings.uploads/(identifier+'.part')).exists())
        identifier = self.start_upload(content, digest='1'*64)['session_id']
        self.assertEqual(self.chunk(identifier, 0, content).status_code, 200)
        self.assertEqual(self.client.post(f'/api/upload-sessions/{identifier}/complete').status_code, 422)
        self.assertEqual(self.client.get('/api/videos').json()['total'], 1)
        self.assertEqual(self.client.post('/api/upload-sessions', json={'filename': 'bad.mp4', 'size_bytes': True, 'sha256': '1'*64}).status_code, 422)

    def test_upload_finalization_failure_keeps_resumable_content(self):
        content = self.content+b'finalize-retry'
        identifier = self.start_upload(content)['session_id']
        self.chunk(identifier, 0, content)
        with patch.object(self.app.state.store, 'public_video', side_effect=RuntimeError('DB response failed')):
            with self.assertRaises(RuntimeError):
                self.client.post(f'/api/upload-sessions/{identifier}/complete')
        self.assertTrue((self.settings.uploads/(identifier+'.part')).exists())
        self.assertEqual(len(list(self.settings.videos.iterdir())), 1)
        self.assertEqual(self.client.post(f'/api/upload-sessions/{identifier}/complete').status_code, 200)

    def test_upload_auto_setting_and_waiting_for_real_configuration(self):
        jobs = self.app.state.analysis
        jobs.dispatch_once()
        video = self.client.get('/api/videos').json()['videos'][0]
        self.assertEqual(video['upload_analysis']['state'], 'waiting_config')
        self.assertEqual(self.client.get('/api/jobs').json()['jobs'], [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.client.patch('/api/settings', json={'auto_analyze_uploads': False}).status_code, 200)
        second = self.another()
        self.assertEqual(second['upload_analysis']['state'], 'disabled')
        with TestClient(create_app(self.settings)) as restarted:
            self.assertFalse(restarted.get('/api/settings').json()['auto_analyze_uploads'])
        self.assertEqual(self.client.patch('/api/settings', json={'auto_analyze_uploads': 1}).status_code, 422)

    def test_batch_skips_annotated_and_queues_persistently(self):
        self.client.patch('/api/settings', json={'auto_analyze_uploads': False})
        second = self.another()
        self.save()
        response = self.client.post('/api/analysis-batches', json={'video_ids': [self.video['id'], second['id']]})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual([item['state'] for item in response.json()['items']], ['skipped', 'pending'])
        self.app.state.analysis.dispatch_once()
        pending = next(v for v in self.client.get('/api/videos').json()['videos'] if v['id']==second['id'])
        self.assertEqual(pending['upload_analysis']['state'], 'waiting_config')
        self.assertEqual(pending['upload_analysis']['origin'], 'batch')
        repeated = self.client.post('/api/analysis-batches', json={'video_ids': [second['id']]}).json()
        self.assertEqual(repeated['items'][0]['state'], 'skipped')
        self.assertEqual(self.client.post('/api/analysis-batches', json={'video_ids': [second['id'], second['id']]}).status_code, 422)

    def test_automatic_dispatch_keeps_revision_and_never_applies(self):
        jobs = self.app.state.analysis
        with patch('local_annotation.inference.availability', return_value={'ready': True}), patch('local_annotation.inference.fingerprint', return_value=('checkpoint', 'backbone')), patch.object(jobs.executor, 'submit') as submitted:
            jobs.dispatch_once()
            self.assertEqual(submitted.call_count, 1)
        with self.app.state.store.connect() as conn:
            row = conn.execute('SELECT * FROM jobs').fetchone()
            self.assertEqual((row['status'], row['origin'], row['base_revision']), ('queued', 'upload', 0))
            self.assertEqual(json.loads(row['model_identity_json']), ['checkpoint', 'backbone'])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.context()['video']['review_state'], 'pending')
        # A new manual edit makes the queued task's eventual result stale.
        result = {'video_sha256': self.video['sha256'], 'model': {}, 'segments': []}
        jobs.update(row['id'], status='succeeded', result_json=encode(result))
        self.save()
        response = self.client.post(f"/api/jobs/{row['id']}/apply", json={'expected_revision': 1, 'replace_existing': True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.rows()[0]['row']['segment_id'], 'segment-a')

    def test_persistent_batch_drains_when_executor_capacity_returns(self):
        jobs = self.app.state.analysis
        self.client.patch('/api/settings', json={'auto_analyze_uploads': False})
        videos = [self.another(str(index).encode()) for index in range(10)]
        response = self.client.post('/api/analysis-batches', json={'video_ids': [video['id'] for video in videos]})
        self.assertEqual(response.status_code, 202)
        with patch('local_annotation.inference.availability', return_value={'ready': True}), patch('local_annotation.inference.fingerprint', return_value=('checkpoint','backbone')), patch.object(jobs.executor, 'submit'):
            jobs.dispatch_once()
            with self.app.state.store.connect(write=True) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0], 8)
                self.assertEqual(conn.execute("SELECT count(*) FROM upload_analysis WHERE state='waiting_capacity'").fetchone()[0], 2)
                conn.execute("UPDATE jobs SET status='failed'")
            jobs.dispatch_once()
            with self.app.state.store.connect() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT count(*) FROM upload_analysis WHERE state='waiting_capacity'").fetchone()[0], 0)
        self.assertEqual(self.rows(), [])

    def test_crop_record_is_persisted_with_real_pixel_intervals_only(self):
        # Synthetic preprocessing validates the adapter/UI contract, not AI.
        result = {'video_sha256': self.video['sha256'], 'video': {'width': 320, 'height': 240}, 'model': {'checkpoint_sha256': 'test-only'},
                  'segments': [], 'preprocess': {'source_type': 'adaptive-fov-v1', 'strategy': 'adaptive-fov-v1', 'intervals': [
                      {'start_sec': 0, 'end_sec': 2, 'box': [20, 30, 280, 220], 'ellipse': [1]},
                      {'start_sec': 2, 'end_sec': 4, 'box': [0, 0, 320, 240], 'ellipse': None}]}}
        route = f"/annotation/api/workflow/videos/{self.video['id']}/crop-record"
        self.assertEqual(self.client.get(route).json()['state'], 'unavailable')
        with self.app.state.store.connect(write=True) as conn:
            video = dict(self.app.state.store.video(conn, self.video['id']))
            conn.execute("INSERT INTO jobs(id,video_id,status,created_at,updated_at) VALUES('crop-test',?,'running',?,?)", (self.video['id'], time.time(), time.time()))
        self.assertEqual(self.client.get(route).json()['state'], 'pending')
        self.app.state.analysis.finish('crop-test', video, result)
        record = self.client.get(route).json()['record']
        self.assertEqual(record['intervals'][0]['points'], [[20,30],[280,30],[280,220],[20,220]])
        self.assertTrue(record['intervals'][1]['fallback'])
        self.assertEqual(record['video_sha256'], self.video['sha256'])
        self.assertEqual(self.rows(), [])
        self.assertIsNone(crop_record({}, video, 'test'))
        result['preprocess']['intervals'][0]['box'][2] = 999
        with self.assertRaises(ValueError):
            crop_record(result, video, 'test')
        result['preprocess']['intervals'][0]['box'][2] = 280
        result['preprocess']['intervals'][1]['start_sec'] = 1
        with self.assertRaises(ValueError):
            crop_record(result, video, 'test')

    def test_bulk_delete_is_fenced_by_profile_and_rolls_back_all_files(self):
        second = self.another()
        ids = [self.video['id'], second['id']]
        body = self.preview(ids)
        self.client.put('/clinical/api/case-profile', params={'case_id': self.video['case_id']}, json={'expected_revision': 0, 'details': {'age': '40'}})
        self.assertEqual(self.client.post('/api/cases/delete', json=body).status_code, 409)
        body = self.preview(ids)
        replace, calls = os.replace, []
        def fail_second(source, target):
            calls.append(str(source))
            if len(calls)==2:
                raise OSError('disk failure on second video')
            return replace(source, target)
        with patch('local_annotation.deletion.os.replace', side_effect=fail_second):
            with self.assertRaises(OSError):
                self.client.post('/api/cases/delete', json=body)
        self.assertEqual(self.client.get('/api/videos').json()['total'], 2)
        self.assertEqual(self.client.get(self.video['media_url']).status_code, 200)
        self.assertEqual(self.client.get(second['media_url']).status_code, 200)
        self.assertEqual(list(self.settings.temporary.glob('delete-*')), [])
        self.assertEqual(self.client.post('/api/cases/delete', json=body).json()['deleted_count'], 2)
        self.assertEqual(self.client.get('/api/videos').json()['total'], 0)
        self.assertEqual(list(self.settings.videos.iterdir()), [])

    def test_whole_case_deletes_frames_results_exports_and_upload_identity(self):
        content = self.content+b'whole-case'
        identifier = self.start_upload(content)['session_id']
        self.chunk(identifier, 0, content)
        new = self.client.post(f'/api/upload-sessions/{identifier}/complete').json()['video']
        directory = self.settings.frames/'frame-record'
        directory.mkdir()
        (directory/'frame.jpg').write_bytes(b'synthetic-file')
        archive_id = 'a'*32
        (self.settings.exports/(archive_id+'.zip')).write_bytes(b'cached-export')
        with self.app.state.store.connect(write=True) as conn:
            conn.execute("INSERT INTO extractions(id,video_id,revision,fps,status,manifest_json,created_at,updated_at) VALUES('frame-record',?,0,1,'succeeded',?,?,?)", (new['id'], encode({'frames': [{}]}), time.time(), time.time()))
            conn.execute('INSERT INTO case_exports VALUES(?,?)', (archive_id, time.time()))
            conn.execute('INSERT INTO export_cases VALUES(?,?)', (archive_id, new['id']))
            conn.execute('INSERT INTO crop_records VALUES(?,?,?,?)', (new['id'], 'old-result', '{}', time.time()))
            conn.execute("INSERT INTO audit(video_id,action,detail_json,created_at) VALUES(?,'old-private-data','{}','test')", (new['id'],))
        response = self.client.post('/api/cases/delete', json=self.preview([new['id']]))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(directory.exists())
        self.assertEqual(self.client.get('/api/exports/'+archive_id).status_code, 404)
        self.assertEqual(self.client.get('/api/upload-sessions/'+identifier).status_code, 410)
        with self.app.state.store.connect() as conn:
            for table in ('annotations', 'jobs', 'extractions', 'crop_records', 'upload_analysis', 'audit'):
                self.assertEqual(conn.execute(f'SELECT count(*) FROM {table} WHERE video_id=?', (new['id'],)).fetchone()[0], 0)
        self.assertEqual(self.start_upload(content)['offset'], 0)

    def test_deletion_rejects_running_tasks_and_recovers_precommit_moves(self):
        with self.app.state.store.connect(write=True) as conn:
            conn.execute("INSERT INTO jobs(id,video_id,status,created_at,updated_at) VALUES('active',?,'queued',?,?)", (self.video['id'], time.time(), time.time()))
        self.assertEqual(self.client.post('/api/cases/delete-preview', json={'video_ids': [self.video['id']]}).status_code, 409)
        with self.app.state.store.connect(write=True) as conn:
            conn.execute("UPDATE jobs SET status='failed' WHERE id='active'")
            video = self.app.state.store.video(conn, self.video['id'])
        directory = self.settings.temporary/'delete-crash-fixture'
        directory.mkdir()
        relative = 'videos/'+video['stored_name']
        (directory/'journal.json').write_text(json.dumps({'ids': [self.video['id']], 'paths': [relative]}))
        os.replace(self.settings.data_dir/relative, directory/'0')
        CaseDeletion(self.app.state.store)
        self.assertEqual(self.client.get(self.video['media_url']).status_code, 200)
        self.assertFalse(directory.exists())

    def test_legacy_cached_export_is_included_in_whole_case_deletion(self):
        identifier = 'b'*32
        path = self.settings.exports/(identifier+'.zip')
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'cases': [{'case_id': self.video['case_id'], 'video': {'sha256': self.video['sha256']}}]}))
        CaseDeletion(self.app.state.store)
        response = self.client.post('/api/cases/delete-preview', json={'video_ids': [self.video['id']]})
        self.assertEqual(response.json()['cached_export_count'], 1)
        self.assertEqual(self.client.post('/api/cases/delete', json=self.preview([self.video['id']])).status_code, 200)
        self.assertFalse(path.exists())


del Base
