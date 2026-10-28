"""Local reviewed-phase extraction and portable archive export."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import threading
import time
from uuid import uuid4
import zipfile

from fastapi import HTTPException

from case_bundle import SCHEMA, MAX_ARCHIVE, MAX_ENTRIES, MAX_MANIFEST, MAX_UNPACKED, canonical, digest_file, seal
from .store import encode, now


def report(case):
    phases=case['rows']['phase']
    findings=case['rows']['finding']
    overview=case['rows']['case'][0] if case['rows']['case'] else {}
    return {'schema':'sam-annotation.structured-report.v1', 'source':'human_annotations',
            'case_id':case['case_id'], 'video_sha256':case['video']['sha256'],
            'demographics':case['profile'].get('details',{}),
            'diagnosis':overview.get('diagnosis_main',''), 'case_notes':overview.get('notes',''),
            'examination_type':overview.get('pre_or_post_op',''),
            'phase_count':len(phases), 'finding_count':len(findings),
            'effective_duration_seconds':sum(float(r['end_sec'])-float(r['start_sec']) for r in phases if r['phase_label']=='effective_observation'),
            'readable_duration_seconds':sum(float(r['end_sec'])-float(r['start_sec']) for r in phases if r['phase_label']=='effective_observation' and r['quality_level']=='2'),
            'phases':phases,'findings':findings,
            'review_state':case['review_state'],'annotation_revision':case['annotation_revision'],
            'note':'结构化整理已保存标注，不生成新的诊断结论；未标注内容保持未知。'}


class Extractions:
    def __init__(self,store):
        self.store=store
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='local-extraction')
        self.stop=threading.Event()
        with store.connect(write=True) as conn:
            conn.execute("UPDATE extractions SET status='interrupted',error='服务已重启，请重新抽帧',updated_at=? WHERE status IN ('queued','running')",(time.time(),))

    def close(self):
        self.stop.set();self.executor.shutdown(wait=True,cancel_futures=True)
        with self.store.connect(write=True) as conn:
            conn.execute("UPDATE extractions SET status='interrupted',error='服务已停止，请重新抽帧',updated_at=? WHERE status IN ('queued','running')",(time.time(),))

    def submit(self,video_id,body):
        fps=body.get('fps',4)
        if type(fps) not in (int,float) or not math.isfinite(fps) or not .1<=fps<=30:
            raise HTTPException(422,'抽帧率须为 0.1–30 fps')
        if not shutil.which('ffmpeg'):
            raise HTTPException(503,'请先在服务端安装 FFmpeg')
        with self.store.connect(write=True) as conn:
            video=self.store.video(conn,video_id)
            if video['revision']!=body.get('expected_revision') or video['review_state']!='reviewed':
                raise HTTPException(409,'请先保存并完成当前视频复核，再抽帧')
            if conn.execute("SELECT 1 FROM extractions WHERE video_id=? AND status IN ('queued','running')",(video_id,)).fetchone():
                raise HTTPException(409,'此视频已有抽帧任务')
            if conn.execute("SELECT count(*) FROM extractions WHERE status IN ('queued','running')").fetchone()[0]>=8:
                raise HTTPException(429,'本机抽帧队列已满')
            phases=[item['row'] for item in self.store.rows(conn,video_id,'phase') if item['row']['phase_label']=='effective_observation' and item['row']['quality_level']=='2']
            count=sum(math.ceil((float(row['end_sec'])-float(row['start_sec']))*fps-1e-8) for row in phases)
            if not 1<=count<=20000:
                raise HTTPException(422,'没有可判读有效片段，或预计超过 20000 张帧；请调整阶段或抽帧率')
            identifier=uuid4().hex
            conn.execute("INSERT INTO extractions(id,video_id,revision,fps,status,created_at,updated_at) VALUES(?,?,?,?,'queued',?,?)",(identifier,video_id,video['revision'],fps,time.time(),time.time()))
        self.executor.submit(self.run,identifier,dict(video),phases,fps,count)
        return {'ok':True,'extraction_id':identifier,'expected_frame_count':count}

    def update(self,identifier,**changes):
        with self.store.connect(write=True) as conn:
            conn.execute('UPDATE extractions SET '+','.join(key+'=?' for key in changes)+',updated_at=? WHERE id=?',(*changes.values(),time.time(),identifier))

    def run(self,identifier,video,phases,fps,total):
        directory=self.store.settings.frames/identifier
        process=None
        try:
            self.update(identifier,status='running')
            directory.mkdir(parents=True)
            frames=[]
            for index,phase in enumerate(phases):
                if self.stop.is_set():
                    raise InterruptedError('服务停止，抽帧已中断')
                start,end=float(phase['start_sec']),float(phase['end_sec'])
                count=math.ceil((end-start)*fps-1e-8)
                # One decoder per segment; no whole-video frame materialization.
                pattern=directory/f'segment_{index:04d}_%06d.jpg'
                command=['ffmpeg','-nostdin','-v','error','-threads','1','-ss',str(start),'-i',str(self.store.settings.videos/video['stored_name']),
                         '-t',str(end-start),'-an','-sn','-vf',f'select=gte(t\\,selected_n/{fps})','-fps_mode','passthrough','-frames:v',str(count),'-q:v','2','-threads','1',str(pattern)]
                with tempfile_log(directory) as log:
                    process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=log)
                    deadline=time.monotonic()+max(120,(end-start)*4)
                    while process.poll() is None:
                        if self.stop.wait(.2):
                            process.terminate();raise InterruptedError('服务停止，抽帧已中断')
                        if time.monotonic()>deadline:
                            process.kill();raise RuntimeError('视频解码超时')
                    if process.returncode:
                        raise RuntimeError('视频无法完整解码，未发布抽帧结果')
                outputs=sorted(directory.glob(f'segment_{index:04d}_*.jpg'))
                if len(outputs)!=count:
                    raise RuntimeError('解码帧数与抽帧计划不符，未发布抽帧结果')
                for number,path in enumerate(outputs):
                    raw=path.read_bytes()
                    frames.append({'filename':path.name,'timestamp_seconds':start+number/fps,'segment_id':phase['segment_id'],
                        'width':video['width'],'height':video['height'],'size_bytes':len(raw),
                        'sha256':hashlib.sha256(raw).hexdigest(),'md5':hashlib.md5(raw).hexdigest()})
                self.update(identifier,progress=len(frames)/total)
            manifest={'schema':'local-extraction.v1','fps':fps,'revision':video['revision'],'frames':frames,'created_at':now()}
            with self.store.connect(write=True) as conn:
                current=self.store.video(conn,video['id'])
                status='succeeded' if current['revision']==video['revision'] and current['review_state']=='reviewed' else 'stale'
                conn.execute('UPDATE extractions SET status=?,progress=1,manifest_json=?,updated_at=? WHERE id=?',(status,encode(manifest),time.time(),identifier))
        except Exception as error:
            shutil.rmtree(directory,ignore_errors=True)
            self.update(identifier,status='interrupted' if isinstance(error,InterruptedError) else 'failed',error=str(error)[:500])
        finally:
            if process is not None and process.poll() is None:
                process.kill();process.wait()

    def export(self,body):
        ids=body.get('video_ids')
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(type(item) is not int for item in ids) or len(set(ids))!=len(ids):
            raise HTTPException(422,'请选择 1–100 个不同病例')
        cases=[];sources=[]
        with self.store.connect() as conn:
            conn.execute('BEGIN')
            instance=conn.execute("SELECT value FROM local_meta WHERE key='instance_id'").fetchone()[0]
            for video_id in ids:
                video=self.store.video(conn,video_id)
                extraction=conn.execute("SELECT * FROM extractions WHERE video_id=? AND status='succeeded' AND revision=? ORDER BY created_at DESC LIMIT 1",(video_id,video['revision'])).fetchone()
                if not extraction or video['review_state']!='reviewed':
                    raise HTTPException(409,'所选病例须全部完成当前版本复核及抽帧；标注更改后需要重新抽帧')
                identifier=hashlib.sha256((instance+':'+video['case_id']).encode()).hexdigest()
                root='cases/'+identifier+'/'
                extracted=json.loads(extraction['manifest_json'])
                frames=[]
                for frame in extracted['frames']:
                    file=self.store.settings.frames/extraction['id']/frame['filename']
                    path=root+'frames/'+frame['filename']
                    frames.append({key:value for key,value in frame.items() if key!='filename'}|{'path':path})
                    sources.append((path,file,frame['sha256'],frame['size_bytes']))
                    if len(sources)+2*len(ids)+1>MAX_ENTRIES:
                        raise HTTPException(413,'所选病例文件总数超过交接包限制，请分批导出')
                case={'portable_id':identifier,'case_id':video['case_id'],
                    'video':{key:value for key,value in self.store.public_video(conn,video).items() if key in {'sha256','size_bytes','original_filename','duration_seconds','width','height','codec'}},
                    'profile':{'details':json.loads(video['profile_json'])},
                    'rows':{table:[item['row'] for item in self.store.rows(conn,video_id,table)] for table in ('case','phase','finding')},
                    'annotation_revision':video['revision'],'review_state':video['review_state'],
                    'extraction':{'id':extraction['id'],'fps':extraction['fps'],'revision':extraction['revision'],'created_at':extracted['created_at']},
                    'frames':frames,'annotation_path':root+'annotations.json','report_path':root+'report.json'}
                cases.append(case)
        if sum(item[3] for item in sources)>MAX_UNPACKED-MAX_MANIFEST:
            raise HTTPException(413,'所选帧总大小超过交接包限制，请分批导出')
        identifier=uuid4().hex
        temporary=self.store.settings.temporary/(identifier+'.zip')
        target=self.store.settings.exports/(identifier+'.zip')
        files=[]
        try:
            with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
                for path,source,digest,size in sources:
                    if not source.is_file() or source.stat().st_size!=size or digest_file(source)!=digest:
                        raise HTTPException(409,'抽帧文件缺失或损坏，请重新抽帧')
                    archive.write(source,path)
                    files.append({'path':path,'sha256':digest,'size_bytes':size})
                for case in cases:
                    for key,value in (('annotation_path',{'rows':case['rows'],'profile':case['profile']}),('report_path',report(case))):
                        raw=canonical(value)
                        archive.writestr(case[key],raw)
                        files.append({'path':case[key],'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw)})
                manifest=seal({'schema':SCHEMA,'cases':cases,'files':files})
                raw_manifest=canonical(manifest)
                if len(raw_manifest)>MAX_MANIFEST:
                    raise HTTPException(413,'病例清单过大，请分批导出')
                archive.writestr('manifest.json',raw_manifest)
            if temporary.stat().st_size>MAX_ARCHIVE:
                raise HTTPException(413,'导出包超过 2 GiB，请减少所选病例或抽帧率')
            temporary.replace(target)
            return {'ok':True,'bundle_id':manifest['bundle_id'],'case_count':len(cases),'frame_count':len(sources),
                    'download_url':'/api/exports/'+identifier,'size_bytes':target.stat().st_size}
        finally:
            temporary.unlink(missing_ok=True)


from contextlib import contextmanager


@contextmanager
def tempfile_log(directory):
    path=directory/'ffmpeg.log'
    try:
        with path.open('wb') as handle:
            yield handle
    finally:
        path.unlink(missing_ok=True)
