"""Generate a nonclinical, real-export ZIP for gateway integration tests."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from local_annotation.app import create_app
from local_annotation.settings import Settings


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);video=root/'fixture.mp4'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=10','-t','4','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(video)],check=True)
        with TestClient(create_app(Settings(root/'data'))) as client:
            v=client.post('/api/videos/upload?filename=synthetic-example.mp4',content=video.read_bytes()).json()['video']
            row={'case_id':v['case_id'],'video_id':str(v['id']),'start_sec':'.5','end_sec':'3','phase_label':'effective_observation','quality_level':'2','visible_side':'left','notes':'合成视频测试，不含患者资料'}
            assert client.post('/clinical/api/rows',json={'table':'phase','row':row,'expected_rows':[]}).status_code==200
            case={'case_id':v['case_id'],'diagnosis_main':'测试诊断','notes':'测试报告'}
            assert client.post('/clinical/api/rows',json={'table':'case','row':case,'expected_rows':[]}).status_code==200
            context=client.get(f"/clinical/api/videos/{v['id']}/editor-context").json()
            assert client.post(f"/annotation/api/workflow/videos/{v['id']}/review",json={'expected_revision':2,'expected_rows':context['rows']['phase']}).status_code==200
            assert client.post(f"/api/videos/{v['id']}/extract",json={'expected_revision':2,'fps':2}).status_code==202
            for _ in range(400):
                state=client.get('/api/videos').json()['videos'][0]['extraction']
                if state['status'] not in {'queued','running'}:break
                time.sleep(.05)
            assert state['status']=='succeeded',state
            exported=client.post('/api/exports',json={'video_ids':[v['id']]}).json()
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_bytes(client.get(exported['download_url']).content)


if __name__=='__main__':main()
