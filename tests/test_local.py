import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from local_annotation.app import create_app
from local_annotation.settings import Settings
from local_annotation.store import encode


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required for video integration tests")
class LocalWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.fixture_dir.name) / "sample.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(cls.fixture)], check=True, capture_output=True)
        cls.content = cls.fixture.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.temp.name))
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        response = self.client.post("/api/videos/upload", params={"filename": "sample.mp4"}, content=self.content)
        self.assertEqual(response.status_code, 201, response.text)
        self.video = response.json()["video"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def rows(self, table="phase"):
        return self.client.get("/clinical/api/rows", params={"table": table, "case_id": self.video["case_id"], "video_id": str(self.video["id"])}).json()["rows"]

    def phase(self, start=0, end=3, **extra):
        return {"case_id": self.video["case_id"], "video_id": str(self.video["id"]), "segment_id": "segment-a",
                "start_sec": str(start), "end_sec": str(end), "phase_label": "effective_observation", "quality_level": "2", "visible_side": "left", **extra}

    def save(self, row=None, table="phase", expected=None):
        return self.client.post("/clinical/api/rows", json={"table": table, "row": row or self.phase(), "expected_rows": self.rows(table) if expected is None else expected})

    def context(self):
        return self.client.get(f"/clinical/api/videos/{self.video['id']}/editor-context").json()

    def test_stream_upload_duplicate_and_media_ranges(self):
        duplicate = self.client.post("/api/videos/upload", params={"filename": "renamed.mp4"}, content=self.content)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["existing_video"]["id"], self.video["id"])
        response = self.client.get(self.video["media_url"], headers={"Range": "bytes=0-31"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, self.content[:32])
        self.assertEqual(self.client.head(self.video["media_url"]).status_code, 200)
        self.assertEqual(len(list(self.settings.videos.iterdir())), 1)
        self.assertEqual(list(self.settings.temporary.iterdir()), [])

    def test_invalid_upload_never_registers_a_video(self):
        for filename, content in (("bad.mp4", b"invalid"), ("bad.mp4", b""), ("bad.txt", b"test")):
            response = self.client.post("/api/videos/upload", params={"filename": filename}, content=content)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/videos").json()["total"], 1)
        self.assertEqual(list(self.settings.temporary.iterdir()), [])

    def test_persistence_profile_and_scoped_context(self):
        self.assertEqual(self.save().status_code, 200)
        profile = self.client.put("/clinical/api/case-profile", params={"case_id": self.video["case_id"]}, json={"expected_revision": 0, "details": {"sex": "female", "age": "35"}})
        self.assertEqual(profile.status_code, 200)
        with TestClient(create_app(self.settings)) as restarted:
            context = restarted.get(f"/clinical/api/videos/{self.video['id']}/editor-context").json()
            self.assertEqual(context["profile"]["details"]["age"], "35")
            self.assertEqual(context["rows"]["phase"][0]["row"]["visible_side"], "left")
            self.assertEqual(context["video"]["revision"], 1)
            wrong = restarted.get("/clinical/api/videos/context", params={"case_id": self.video["case_id"], "video_id": "999"})
            self.assertEqual(wrong.status_code, 404)

    def test_stale_save_is_rejected_and_overlay_keeps_tails(self):
        self.assertEqual(self.save().status_code, 200)
        stale = self.save(self.phase(1, 2, segment_id="segment-b"), expected=[])
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.save(self.phase(1, 2, segment_id="segment-b", phase_label="unused")).status_code, 200)
        rows = self.rows()
        self.assertEqual([(float(item["row"]["start_sec"]), float(item["row"]["end_sec"])) for item in rows], [(0, 1), (1, 2), (2, 3)])
        self.assertEqual(len({item["row"]["segment_id"] for item in rows}), 3)
        self.assertEqual(rows[1]["row"]["quality_level"], "")

    def test_rejects_nonfinite_out_of_bounds_and_cross_video_updates(self):
        for start, end in (("nan", 2), (0, "inf"), (-1, 1), (1, 1), (0, 5)):
            self.assertEqual(self.save(self.phase(start, end)).status_code, 422)
        self.assertEqual(self.save(self.phase(video_id="999")).status_code, 404)
        self.assertEqual(self.rows(), [])

    def test_atomic_history_restore_and_conflict(self):
        self.save()
        previous = self.rows()
        self.save(self.phase(1, 2, segment_id="segment-b"))
        current = self.rows()
        body = {"case_id": self.video["case_id"], "video_id": str(self.video["id"]), "table": "phase", "expected_rows": current, "restore_rows": previous}
        response = self.client.post("/clinical/api/annotation-history", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([r["row"] for r in self.rows()], [r["row"] for r in previous])
        self.assertEqual(self.client.post("/clinical/api/annotation-history", json=body).status_code, 409)
        invalid = {**body, "expected_rows": self.rows(), "restore_rows": [{"row": self.phase(0, 99)}]}
        self.assertEqual(self.client.post("/clinical/api/annotation-history", json=invalid).status_code, 422)
        self.assertEqual(len(self.rows()), 1)

    def test_finding_binds_to_effective_phase(self):
        self.save()
        phase = self.rows()[0]
        row = {"case_id": self.video["case_id"], "video_id": str(self.video["id"]), "start_sec": "0", "end_sec": "3", "clip_id": "finding-a", "phase_segment_id": "segment-a", "mucosal_swelling": "1"}
        response = self.client.post("/clinical/api/rows", json={"table": "finding", "row": row, "expected_rows": [], "phase_ref": {"row_id": phase["index"], "expected_row": phase["row"]}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.rows("finding")[0]["row"]["clip_quality"], "2")
        self.assertEqual(self.save({**row, "phase_segment_id": "missing"}, table="finding").status_code, 409)

    def test_review_is_invalidated_after_edit(self):
        self.save()
        body = {"expected_revision": self.context()["video"]["revision"], "expected_rows": self.rows()}
        route = f"/annotation/api/workflow/videos/{self.video['id']}/review"
        self.assertEqual(self.client.post(route, json=body).json()["video"]["review_state"], "reviewed")
        self.assertTrue(self.client.post(route, json=body).json()["unchanged"])
        self.save(self.phase(3, 4, segment_id="segment-b"))
        self.assertEqual(self.context()["video"]["review_state"], "pending")
        self.assertEqual(self.client.post(route, json=body).status_code, 409)

    def test_exports_preserve_unicode_and_original_headers(self):
        self.save(self.phase(notes="中文备注"))
        payload = self.client.get(f"/api/videos/{self.video['id']}/annotations.json").json()
        self.assertEqual(payload["rows"]["phase"][0]["row"]["notes"], "中文备注")
        archive = zipfile.ZipFile(io.BytesIO(self.client.get("/clinical/export.zip").content))
        self.assertEqual(set(archive.namelist()), {"annotations.json", "doctor_case_annotation.csv", "doctor_video_phase_annotation.csv", "doctor_effective_clip_finding_annotation.csv"})
        self.assertIn("中文备注", archive.read("doctor_video_phase_annotation.csv").decode("utf-8-sig"))

    def test_ai_missing_weights_has_no_fake_results(self):
        response = self.client.post(f"/api/videos/{self.video['id']}/analyze")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.client.get("/api/jobs").json()["jobs"], [])

    def test_ai_apply_is_explicit_versioned_and_keeps_findings_safe(self):
        # A synthetic stored result tests persistence policy, not model accuracy.
        result = {"video_sha256": self.video["sha256"], "model": {}, "segments": [{"start_sec": 1.0, "end_sec": 2.0, "quality_pred": 3, "side_pred": 2}], "summary": {"segment_count": 1}}
        with self.app.state.store.connect(write=True) as conn:
            conn.execute("INSERT INTO jobs(id,video_id,status,result_json,created_at,updated_at) VALUES(?,?,'succeeded',?,?,?)", ("test-result", self.video["id"], encode(result), time.time(), time.time()))
        self.assertEqual(self.client.post("/api/jobs/test-result/apply", json={"expected_revision": 99}).status_code, 409)
        response = self.client.post("/api/jobs/test-result/apply", json={"expected_revision": 0})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([r["row"]["phase_label"] for r in self.rows()], ["unused", "effective_observation", "unused"])
        self.assertEqual(self.rows()[1]["row"]["quality_level"], "2")
        self.assertEqual(self.context()["video"]["review_state"], "pending")
        self.assertEqual(self.client.post("/api/jobs/test-result/apply", json={"expected_revision": 1}).status_code, 409)

    def test_delete_requires_current_revision_and_filename_and_never_reuses_id(self):
        route = f"/api/videos/{self.video['id']}"
        self.save()
        body = {"expected_revision": 0, "confirm_filename": "sample.mp4"}
        self.assertEqual(self.client.request("DELETE", route, json=body).status_code, 409)
        body["expected_revision"] = 1
        with patch("local_annotation.deletion.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.client.request("DELETE", route, json=body)
        self.assertEqual(self.client.get(self.video["media_url"]).status_code, 200)
        self.assertEqual(self.client.request("DELETE", route, json=body).status_code, 200)
        self.assertEqual(self.client.get(self.video["media_url"]).status_code, 404)
        self.assertEqual(len(list(self.settings.videos.iterdir())), 0)
        new = self.client.post("/api/videos/upload", params={"filename": "again.mp4"}, content=self.content).json()["video"]
        self.assertGreater(new["id"], self.video["id"])

    def test_cross_origin_writes_are_rejected(self):
        response = self.client.post("/api/videos/upload?filename=test.mp4", content=self.content, headers={"Origin": "https://unrelated.example"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
