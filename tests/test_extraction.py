import json
from pathlib import Path
import tempfile
import subprocess
import time
import unittest
import zipfile

from case_bundle import BundleError, inspect_archive
from test_local import LocalWorkflowTests


class ExtractionTests(LocalWorkflowTests):
    # Reuse the isolated video fixture without inheriting the original test cases.
    def reviewed(self):
        self.assertEqual(self.save(self.phase(.5,3)).status_code,200)
        context=self.context()
        response=self.client.post(f"/annotation/api/workflow/videos/{self.video['id']}/review",json={'expected_revision':context['video']['revision'],'expected_rows':context['rows']['phase']})
        self.assertEqual(response.status_code,200)

    def extract(self,fps=4):
        response=self.client.post(f"/api/videos/{self.video['id']}/extract",json={'expected_revision':self.context()['video']['revision'],'fps':fps})
        self.assertEqual(response.status_code,202,response.text)
        for _ in range(200):
            state=self.context()['video']['extraction']
            if state['status'] not in {'queued','running'}:break
            time.sleep(.05)
        self.assertEqual(state['status'],'succeeded',state)
        return state

    def test_reviewed_sampling_package_and_report(self):
        self.reviewed();state=self.extract()
        self.assertEqual(state['frame_count'],10)
        response=self.client.post('/api/exports',json={'video_ids':[self.video['id']]})
        self.assertEqual(response.status_code,200,response.text)
        archive=Path(self.temp.name)/'bundle.zip'
        archive.write_bytes(self.client.get(response.json()['download_url']).content)
        manifest=inspect_archive(archive,Path(self.temp.name)/'inspected')
        case=manifest['cases'][0]
        self.assertEqual([f['timestamp_seconds'] for f in case['frames']],[.5+i/4 for i in range(10)])
        self.assertEqual(case['video']['sha256'],self.video['sha256'])
        report=json.loads((Path(self.temp.name)/'inspected'/case['report_path']).read_text())
        self.assertEqual(report['readable_duration_seconds'],2.5)
        self.assertEqual(report['source'],'human_annotations')
        self.assertNotIn('source.mp4',zipfile.ZipFile(archive).namelist())
        # .75 seconds falls between 10 fps source frames. Match accurate FFmpeg
        # seeking rather than a nearest-frame FPS filter that can pick .70.
        reference=subprocess.check_output(['ffmpeg','-nostdin','-v','error','-threads','1','-ss','0.75','-i',str(self.fixture),'-frames:v','1','-q:v','2','-threads','1','-f','image2pipe','-vcodec','mjpeg','-'])
        actual=(Path(self.temp.name)/'inspected'/case['frames'][1]['path']).read_bytes()
        self.assertEqual(actual,reference)

    def test_low_quality_unreviewed_and_stale_exports_rejected(self):
        route=f"/api/videos/{self.video['id']}/extract"
        self.assertEqual(self.client.post(route,json={'expected_revision':0}).status_code,409)
        self.reviewed();self.extract()
        self.assertEqual(self.save(self.phase(3,4,segment_id='tail')).status_code,200)
        self.assertEqual(self.context()['video']['extraction']['status'],'stale')
        self.assertEqual(self.client.post('/api/exports',json={'video_ids':[self.video['id']]}).status_code,409)

    def test_package_rejects_undeclared_paths_and_damage(self):
        self.reviewed();self.extract(1)
        result=self.client.post('/api/exports',json={'video_ids':[self.video['id']]}).json()
        source=Path(self.temp.name)/'bundle.zip';source.write_bytes(self.client.get(result['download_url']).content)
        with zipfile.ZipFile(source,'a') as archive:archive.writestr('../escape.txt',b'bad')
        with self.assertRaises(BundleError):inspect_archive(source,Path(self.temp.name)/'unsafe')
        self.assertFalse((Path(self.temp.name)/'escape.txt').exists())


# unittest otherwise runs every test inherited from the fixture as well.
for name in list(vars(LocalWorkflowTests)):
    if name.startswith('test_'):
        setattr(ExtractionTests,name,None)
del LocalWorkflowTests


if __name__=='__main__':unittest.main()
