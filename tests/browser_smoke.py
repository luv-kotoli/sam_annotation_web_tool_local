"""Real browser workflow against an isolated app and generated, nonclinical videos.

Run using a Python environment with Playwright and its Chromium installed.
LOCAL_APP_PYTHON may point to the application's Python interpreter.
"""
from contextlib import contextmanager
import json
import hashlib
import sqlite3
import struct
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect
expect.set_options(timeout=25000)

PROJECT = Path(__file__).resolve().parents[1]


@contextmanager
def failure_artifacts(page, artifacts, errors, bad_requests, external):
    try:
        yield
    except BaseException as error:
        report = {"passed": False, "failure": str(error), "browser_errors": errors,
                  "failed_requests": bad_requests, "external_requests": external}
        if os.environ.get("GITHUB_ACTIONS") == "true":
            detail = str(error)[:12000].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::error title=Browser annotation workflow::" + detail, flush=True)
        try:
            page.screenshot(path=str(artifacts / "failure.png"), full_page=True, timeout=5000)
        except Exception as capture_error:
            report["screenshot_error"] = str(capture_error)
        try:
            page.context.tracing.stop(path=str(artifacts / "trace.zip"))
        except Exception as trace_error:
            report["trace_error"] = str(trace_error)
        (artifacts / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        raise


def main():
    artifacts = PROJECT / "test-artifacts/browser"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="local-video-browser-") as temporary:
        root = Path(temporary)
        for name, color in (("demo-one.mp4", "0x4b7866"), ("demo-two.mp4", "0x78664b")):
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:size=640x480:rate=10", "-t", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(root / name)], check=True)
        with (root/'demo-one.mp4').open('ab') as handle:
            # Valid MP4 free space exercises multiple 8 MiB transport chunks.
            handle.write(struct.pack('>I4s', 9*1024**2, b'free'))
            handle.write(bytes(9*1024**2-8))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        local_base = f"http://127.0.0.1:{port}"
        # A mapped LAN-style origin is deliberately not a secure context.
        base = f"http://annotation-lan.test:{port}"
        log = (artifacts / "server.log").open("w")
        server = subprocess.Popen([os.environ.get("LOCAL_APP_PYTHON", sys.executable), "run.py", "--port", str(port), "--data-dir", str(root / "data")], cwd=PROJECT, stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(local_base + "/health", timeout=1)
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("App failed to start; see server.log")
                    time.sleep(.1)
            else:
                raise TimeoutError("App startup timed out")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(args=["--host-resolver-rules=MAP annotation-lan.test 127.0.0.1", "--no-proxy-server"])
                context = browser.new_context(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
                context.tracing.start(screenshots=True, snapshots=True, sources=False)
                page = context.new_page()
                errors, bad_requests, external = [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("response", lambda response: bad_requests.append({"url": response.url, "status": response.status}) if response.status >= 400 else None)
                page.on("request", lambda request: external.append(request.url) if not request.url.startswith(base) and not request.url.startswith(local_base) and not request.url.startswith("blob:") else None)
                with failure_artifacts(page, artifacts, errors, bad_requests, external):
                    page.goto(base)
                    expect(page.get_by_text("从第一段视频开始")).to_be_visible()
                    assert not page.evaluate('window.isSecureContext')
                    assert not page.evaluate('!!window.crypto.subtle')
                    expect(page.locator('#autoAnalyze')).to_be_checked()
                    page.locator('#autoAnalyze').uncheck()
                    expect(page.locator('#notice')).to_contain_text('已关闭上传后自动分析')
                    # Seed a partial upload, then re-select it in the browser.
                    content = (root/'demo-one.mp4').read_bytes()
                    seeded = context.request.post(local_base+'/api/upload-sessions', data={
                        'filename': 'demo-one.mp4', 'size_bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}).json()
                    prefix = content[:1000]
                    assert context.request.put(local_base+'/api/upload-sessions/'+seeded['session_id'], data=prefix,
                        headers={'Content-Type': 'application/octet-stream', 'Upload-Offset': '0', 'X-Chunk-Sha256': hashlib.sha256(prefix).hexdigest()}).ok
                    lost_ack = []
                    def lose_first_ack(route):
                        if route.request.method == 'PUT' and not lost_ack:
                            lost_ack.append(route.request.headers['upload-offset'])
                            response = route.fetch(url=route.request.url.replace(base, local_base),
                                headers={key:value for key,value in route.request.headers.items() if key not in {'origin','host'}})
                            assert response.ok
                            route.abort('failed')
                        else:
                            route.continue_()
                    page.route('**/api/upload-sessions/*', lose_first_ack)
                    page.locator("#uploadInput").set_input_files([root / "demo-one.mp4", root / "demo-two.mp4"])
                    expect(page.locator(".video-card")).to_have_count(2, timeout=20000)
                    assert lost_ack == ['1000'], lost_ack
                    page.unroute('**/api/upload-sessions/*', lose_first_ack)
                    page.locator('#selectPage').check()
                    page.locator('#batchAnalyze').click()
                    expect(page.locator('#notice')).to_contain_text('已加入 2 个分析等待任务')
                    expect(page.locator('.analysis-state').first).to_contain_text('等待配置本机 AI 模型')
                    page.locator('#selectPage').uncheck()
                    page.locator('#autoAnalyze').check()
                    expect(page.locator('#notice')).to_contain_text('后续上传完成后将自动分析')
                    assert context.request.get(local_base+'/api/settings').json()['auto_analyze_uploads']
                    # A synthetic crop record exercises player geometry only.
                    videos = context.request.get(local_base+'/api/videos').json()['videos']
                    first_video = next(video for video in videos if video['original_filename']=='demo-one.mp4')
                    record = {'schema': 'sam-workflow.video-crop.v1', 'job_id': 'synthetic-browser-fixture',
                              'video': {'width': 640, 'height': 480}, 'video_sha256': first_video['sha256'],
                              'source': 'synthetic-test', 'intervals': [
                                  {'start_sec': 0, 'end_sec': 2, 'points': [[40,30],[600,30],[600,440],[40,440]]},
                                  {'start_sec': 2, 'end_sec': 5, 'points': [[80,60],[560,60],[560,420],[80,420]]}]}
                    with sqlite3.connect(root/'data/annotations.sqlite3') as connection:
                        connection.execute('INSERT INTO crop_records VALUES(?,?,?,?)', (first_video['id'], 'synthetic-browser-fixture', json.dumps(record), time.time()))
                    page.screenshot(path=str(artifacts / "library.png"), full_page=True)
                    page.locator(".open-editor").last.click()
                    page.wait_for_function("document.getElementById('workspace').getAttribute('aria-busy') === 'false'")
                    expect(page.locator("#notice")).to_be_hidden()
                    page.wait_for_function("document.getElementById('videoPlayer').readyState >= 1")
                    expect(page.locator("#videoName")).to_have_text("demo-one.mp4")
                    expect(page.locator('#toggleCropOverlay')).to_be_visible()
                    expect(page.locator('#videoCropOverlay')).to_be_visible()
                    expect(page.locator('#videoCropOverlay polygon')).to_have_attribute('points', '40,30 600,30 600,440 40,440')
                    page.locator('#videoPlayer').evaluate('video => {video.currentTime=2.5;}')
                    expect(page.locator('#videoCropOverlay polygon')).to_have_attribute('points', '80,60 560,60 560,420 80,420')
                    page.locator('#toggleCropOverlay').click()
                    expect(page.locator('#videoCropOverlay')).to_be_hidden()
                    page.locator('#toggleCropOverlay').click()
                    expect(page.locator('#videoCropOverlay')).to_be_visible()
                    page.locator('#videoPlayer').evaluate('video => {video.currentTime=0;}')
                    page.locator("#startSec").evaluate("input => {input.value = '0.5';input.dispatchEvent(new Event('input',{bubbles:true}));}")
                    page.locator("#endSec").evaluate("input => {input.value = '3';input.dispatchEvent(new Event('input',{bubbles:true}));}")
                    page.locator("#annotationNotes").fill("本地版浏览器验证")
                    page.locator("#savePageAnnotations").click()
                    expect(page.locator("#recordCount")).to_have_text("1")
                    expect(page.locator("#saveState")).to_have_text("标注已保存")
                    page.locator("#diagnosisMain").fill("浏览器测试诊断")
                    page.locator("#patientAge").fill("36")
                    page.locator("#saveCase").click()
                    expect(page.locator("#caseSaveState")).to_have_text("已保存")
                    # Both fields and source video must survive a browser reload.
                    page.reload()
                    page.wait_for_function("document.getElementById('workspace').getAttribute('aria-busy') === 'false'")
                    expect(page.locator("#diagnosisMain")).to_have_value("浏览器测试诊断")
                    expect(page.locator("#patientAge")).to_have_value("36")
                    expect(page.locator("#recordCount")).to_have_text("1")
                    page.locator("#annotationNotes").fill("修改后保存")
                    page.locator("#savePageAnnotations").click()
                    expect(page.locator("#saveState")).to_have_text("标注已保存")
                    page.locator("#undoAnnotation").click()
                    expect(page.locator("#annotationNotes")).to_have_value("本地版浏览器验证")
                    page.locator("#redoAnnotation").click()
                    expect(page.locator("#annotationNotes")).to_have_value("修改后保存")
                    page.locator('input[name="choice_mucosal_swelling"]').check()
                    page.locator("#savePageAnnotations").click()
                    expect(page.locator("#saveState")).to_have_text("标注已保存")
                    video_id = page.url.split('workflow_video_id=')[1].split('&')[0]
                    snapshot = context.request.get(local_base + f"/clinical/api/videos/{video_id}/editor-context").json()
                    assert snapshot['rows']['finding'][0]['row']['mucosal_swelling'] == '1'
                    # Delete one complete segment, then restore both linked tables.
                    page.locator('[data-delete-segment]').first.click()
                    page.locator('#confirmOk').click()
                    expect(page.locator('#recordCount')).to_have_text('0')
                    page.locator('#undoAnnotation').click()
                    expect(page.locator('#recordCount')).to_have_text('1')
                    restored = context.request.get(local_base + f"/clinical/api/videos/{video_id}/editor-context").json()
                    assert len(restored['rows']['phase']) == len(restored['rows']['finding']) == 1
                    page.locator("#reviewButton").click()
                    expect(page.locator("#confirmDialog")).to_be_visible()
                    page.locator("#confirmOk").click()
                    expect(page.locator("#reviewStatus")).to_have_text("已完成复核")
                    with page.expect_download() as download:
                        page.locator("#exportButton").click()
                    assert download.value.suggested_filename.endswith(".zip")
                    page.screenshot(path=str(artifacts / "editor.png"), full_page=True)
                    page.locator("#nextVideo").click()
                    expect(page.locator("#videoName")).to_have_text("demo-two.mp4")
                    expect(page.locator("#recordCount")).to_have_text("0")
                    expect(page.locator("#patientAge")).to_have_value("")
                    page.locator("#previousVideo").click()
                    expect(page.locator("#videoName")).to_have_text("demo-one.mp4")
                    expect(page.locator("#recordCount")).to_have_text("1")
                    page.goto(base)
                    selected=page.locator('.video-card').filter(has=page.get_by_role('heading',name='demo-one.mp4'))
                    selected.locator('[data-action="extract"]').click()
                    expect(selected.locator('.extraction-state')).to_contain_text('已抽取 10 帧',timeout=40000)
                    selected.locator('[data-select]').check()
                    with page.expect_download() as bundle_download:
                        page.locator('#exportCases').click()
                    bundle_download.value.save_as(artifacts/'browser-export.zip')
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.goto(base)
                    expect(page.locator(".video-card")).to_have_count(2)
                    page.screenshot(path=str(artifacts / "library-mobile.png"), full_page=True)
                    if page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"):
                        overflowing = page.evaluate("""() => [...document.body.querySelectorAll('*')].flatMap(element => {
                            const box = element.getBoundingClientRect();
                            return box.width && (box.right > innerWidth + 1 || box.left < -1)
                                ? [{tag:element.tagName,id:element.id,class:element.className,
                                    left:box.left,right:box.right,width:box.width,text:element.textContent.slice(0,120)}] : [];
                        }).slice(0,15)""")
                        (artifacts / "layout-overflow.json").write_text(json.dumps(overflowing, ensure_ascii=False, indent=2))
                        raise AssertionError("Mobile horizontal overflow: " + json.dumps(overflowing, ensure_ascii=False))
                    page.locator('#selectPage').check()
                    page.locator('#batchDelete').click()
                    expect(page.locator('#confirmMessage')).to_contain_text('2 个病例')
                    page.get_by_role('button', name='取消', exact=True).click()
                    expect(page.locator('.video-card')).to_have_count(2)
                    page.locator('#batchDelete').click()
                    page.locator('#confirmOk').click()
                    expect(page.locator('.video-card')).to_have_count(0)
                    expect(page.locator('#notice')).to_contain_text('已整例删除 2 个病例')
                    assert context.request.get(local_base+'/api/videos').json()['total']==0
                    assert not list((root/'data/exports').glob('*.zip'))
                    assert not list((root/'data/frames').glob('*'))
                    report = {"passed": not (errors or bad_requests or external), "browser_errors": errors, "failed_requests": bad_requests, "external_requests": external,
                              "checks": ["insecure LAN HTTP without Web Crypto", "resumable upload from saved offset", "lost chunk acknowledgement recovery", "auto analysis setting persistence", "batch AI waiting for real models", "synthetic crop overlay with seeking and toggle", "cancel and confirm whole-case batch deletion", "cached frames and export cleanup", "batch upload", "video metadata/playback", "phase save", "case save", "reload persistence", "undo/redo", "finding save", "segment delete and atomic undo", "review", "ZIP download", "video navigation", "reviewed frame extraction", "selected case ZIP export", "mobile library", "no external dependencies"]}
                    (artifacts / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
                    assert report["passed"], report
                    context.tracing.stop(path=str(artifacts / "trace.zip"))
                    browser.close()
                    print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            log.close()


if __name__ == "__main__":
    main()
