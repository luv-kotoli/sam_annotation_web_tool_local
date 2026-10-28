"""Import the actual browser-exported ZIP into an isolated gateway in Chromium."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright,expect

PROJECT=Path(__file__).resolve().parents[1]
GATEWAY=Path(os.getenv('ANNOTATION_GATEWAY_CHECKOUT','/root/sam_annotation_web_tool'))


def main():
    artifacts=PROJECT/'test-artifacts/transfer';artifacts.mkdir(parents=True,exist_ok=True)
    bundle=PROJECT/'test-artifacts/browser/browser-export.zip'
    assert bundle.is_file(),'Run browser_smoke.py first'
    with tempfile.TemporaryDirectory(prefix='gateway-transfer-browser-') as temporary:
        folder=Path(temporary)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        env={**os.environ,'ANNOTATION_DATA_DIR':str(folder/'data'),'ANNOTATION_USERNAME':'admin',
             'ANNOTATION_PASSWORD':'transfer-test-only','ANNOTATION_SECRET_KEY':'transfer-isolated-test-secret',
             'ANNOTATION_BACKGROUND_WORKERS':'0','ANNOTATION_ENVIRONMENT':'debug'}
        with (artifacts/'gateway.log').open('w') as log:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--workers','1'],cwd=GATEWAY,env=env,stdout=log,stderr=log)
            try:
                for _ in range(100):
                    try:urllib.request.urlopen(base+'/health',timeout=1).close();break
                    except OSError:
                        if server.poll() is not None:raise RuntimeError('Gateway failed to start')
                        time.sleep(.1)
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    context=browser.new_context(viewport={'width':1600,'height':1050})
                    page=context.new_page();errors=[];bad=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('response',lambda r:bad.append({'url':r.url,'status':r.status}) if r.status>=400 else None)
                    page.goto(base+'/login')
                    page.locator('[name=username]').fill('admin');page.locator('[name=password]').fill('transfer-test-only');page.locator('button[type=submit]').click()
                    page.goto(base+'/annotation/')
                    with page.expect_file_chooser() as chooser:page.locator('#importCaseBundle').click()
                    chooser.value.set_files(bundle)
                    expect(page.locator('.fb-card')).to_have_count(1,timeout=30000)
                    expect(page.locator('.fb-card')).to_contain_text('本地导入')
                    page.screenshot(path=str(artifacts/'imported-frame-case.png'),full_page=True)
                    headers={'X-Requested-With':'XMLHttpRequest'}
                    videos=context.request.get(base+'/annotation/api/workflow/videos',headers=headers).json()['videos']
                    video=videos[0];assert video['imported_bundle'] and video['active_frame_count']==10
                    page.goto(base+video['editor_url'])
                    expect(page.get_by_text('本地导入 · 只读',exact=True)).to_be_visible()
                    expect(page.locator('video,canvas,input,textarea,select')).to_have_count(0)
                    expect(page.locator('body')).to_contain_text('浏览器测试诊断')
                    page.screenshot(path=str(artifacts/'readonly-case.png'),full_page=True)
                    page.get_by_role('link',name='进入图像帧标注').click()
                    expect(page.locator('.frame-card')).to_have_count(10,timeout=20000)
                    expect(page.locator('#frameCaseContext')).to_contain_text('本地导入')
                    # Verify the existing frame annotation API accepts real geometry.
                    frames=context.request.get(base+'/annotation/api/frames',headers=headers,params={'video_id':video['id']}).json()['frames']
                    frame=frames[0]
                    task_url=base+f"/annotation/api/label-studio/frames/{frame['id']}"
                    task=context.request.get(task_url,headers=headers).json()
                    region={'id':'imported-rectangle','from_name':'rectangle','to_name':'image','type':'rectanglelabels',
                        'original_width':640,'original_height':480,'image_rotation':0,
                        'value':{'x':10,'y':10,'width':20,'height':20,'rotation':0,'rectanglelabels':['鼻中隔']}}
                    saved=context.request.put(task_url,headers=headers,data={'revision':task['revision'],'result':[region]})
                    assert saved.status==200,saved.text()
                    # Open the actual editor and verify that region is visible/editable.
                    page.locator('.frame-card').first.dblclick()
                    page.wait_for_selector('iframe[src*="label-studio"]',timeout=15000)
                    iframe=page.frame_locator('iframe[src*="label-studio"]')
                    expect(iframe.locator('#status')).to_contain_text('已载入',timeout=30000)
                    expect(iframe.locator('.platform-region-row')).to_have_count(1)
                    page.screenshot(path=str(artifacts/'imported-frame-editor.png'),full_page=True)
                    report={'passed':not errors and not bad,'browser_errors':errors,'failed_requests':bad,
                            'checks':['actual local browser ZIP imported without unpacking','import label','10 frames registered',
                                      'readonly clinical snapshot without video or timeline','original findings/report preserved',
                                      'frame geometry saved through existing API','existing frame editor opens imported geometry']}
                    (artifacts/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                    assert report['passed'],report
                    browser.close();print(json.dumps(report,ensure_ascii=False,indent=2))
            finally:
                server.terminate();server.wait(timeout=15)


if __name__=='__main__':main()
