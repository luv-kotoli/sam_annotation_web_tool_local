(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {videos:[], offset:0, limit:12, total:0, analysis:false, uploading:false, paused:false, pendingFiles:[], generation:0, selected:new Set(), cancelTransfer:null};
  function updateSelection() {
    for (const [id,label] of [['exportCases','导出所选病例包'],['batchAnalyze','批量 AI 分析'],['batchDelete','整例删除']]) {
      $(id).disabled=!state.selected.size;$(id).textContent=`${label}（${state.selected.size}）`;
    }
    $('selectPage').checked=!!state.videos.length&&state.videos.every(video=>state.selected.has(video.id));
  }
  function extractionText(item) {
    if(!item)return '尚未抽帧';
    const labels={queued:'等待抽帧',running:'正在抽帧',succeeded:`已抽取 ${item.frame_count} 帧`,stale:'标注已变化，需要重新抽帧',failed:'抽帧失败',interrupted:'抽帧已中断'};
    return (labels[item.status]||item.status)+(item.status==='running'?` ${Math.round(item.progress*100)}%`:'')+(item.error?'：'+item.error:'');
  }
  const analysisLabels={queued:'等待 AI 分析',running:'正在 AI 分析',succeeded:'AI 分析完成 · 待应用',failed:'AI 分析失败',interrupted:'AI 已中断，可重新分析',applied:'AI 已应用 · 待人工复核'};
  function analysisText(video) {
    const request=video.upload_analysis;
    if(video.analysis&&!['pending','waiting_config','waiting_capacity'].includes(request?.state))return (analysisLabels[video.analysis.status]||video.analysis.status)+(video.analysis.status==='running'?` ${Math.round(video.analysis.progress*100)}%`:'');
    if(!request)return '';
    const labels={pending:'等待自动调度',waiting_config:'等待配置本机 AI 模型',waiting_capacity:'等待 AI 队列空位',submitted:'已提交 AI 分析',disabled:'上传后自动分析已关闭',skipped:'已跳过自动分析',failed:'AI 调度失败'};
    return (request.origin==='batch'?'批量分析 · ':'')+(labels[request.state]||request.state)+(request.detail&&['skipped','failed'].includes(request.state)?'：'+request.detail:'');
  }
  async function api(url, options={}) {
    const response=await fetch(url,{...options,headers:{'Content-Type':'application/json',...options.headers}});
    const data=await response.json();
    if(!response.ok){const error=Error(typeof data.detail==='string'?data.detail:data.detail?.message||'操作失败，请重试');error.status=response.status;throw error;}
    return data;
  }
  function notice(message,error=false){$('notice').textContent=message;$('notice').hidden=!message;$('notice').classList.toggle('error',error);}
  function duration(value){return `${Math.floor(value/60)}:${String(Math.floor(value%60)).padStart(2,'0')}`;}
  function size(value){return value>=1024**3?`${(value/1024**3).toFixed(1)} GB`:`${(value/1024**2).toFixed(1)} MB`;}
  async function confirmAction(title,message,label='确认') {
    $('confirmTitle').textContent=title;$('confirmMessage').textContent=message;$('confirmOk').textContent=label;
    $('confirmDialog').returnValue='';$('confirmDialog').showModal();
    return new Promise(resolve=>$('confirmDialog').addEventListener('close',()=>resolve($('confirmDialog').returnValue==='confirm'),{once:true}));
  }
  async function load() {
    const generation=++state.generation;
    try {
      const data=await api('/api/videos?'+new URLSearchParams({q:$('search').value,offset:state.offset,limit:state.limit}));
      if(generation!==state.generation)return;
      state.videos=data.videos;state.total=data.total;
      if(!data.videos.length&&state.offset>0){state.offset=Math.max(0,state.offset-state.limit);return load();}
      $('videoCount').textContent=data.total;$('emptyState').hidden=!!data.total;
      $('previousPage').disabled=!state.offset;$('nextPage').disabled=state.offset+state.limit>=data.total;
      $('pageLabel').textContent=data.total?`${state.offset+1}–${Math.min(state.offset+state.limit,data.total)} / ${data.total}`:'暂无视频';
      $('videoList').innerHTML=data.videos.map(video=>`<article class="video-card" data-id="${video.id}">
        <a class="video-preview" href="${esc(video.editor_url)}" aria-label="标注 ${esc(video.original_filename)}"><span class="video-number">VIDEO ${String(video.id).padStart(3,'0')}</span><span class="play-symbol">▷</span><span class="video-duration">${duration(video.duration_seconds)}</span></a>
        <div class="video-content"><label class="case-selection"><input type="checkbox" data-select="${video.id}" ${state.selected.has(video.id)?'checked':''}>选择病例</label><h3 title="${esc(video.original_filename)}">${esc(video.original_filename)}</h3><span class="case-number">${esc(video.case_id)}</span><div class="video-meta"><span>${video.width} × ${video.height}</span><span>${size(video.size_bytes)}</span><span>${esc(video.codec)}</span></div><div class="video-status"><span class="state-badge ${video.review_state==='reviewed'?'reviewed':''}">${video.review_state==='reviewed'?'已完成复核':'待复核'}</span><span>${video.phase_count} 个阶段片段</span></div><div class="extraction-state">${esc(extractionText(video.extraction))}</div><div class="analysis-state">${esc(analysisText(video))}</div></div>
        <div class="video-actions"><a class="open-editor" href="${esc(video.editor_url)}">进入标注 ↗</a><button class="small-action" data-action="${video.analysis?.status==='succeeded'?'apply':'analyze'}" ${video.analysis?.status==='succeeded'||state.analysis&&!['queued','running'].includes(video.analysis?.status)?'':'disabled'} title="${video.analysis?.status==='succeeded'?'查看并确认应用 AI 结果':state.analysis?'使用本机阶段模型分析视频':'阶段模型尚未就绪'}">${video.analysis?.status==='succeeded'?'应用 AI':'AI 分期'}</button><button class="small-action" data-action="extract" ${video.review_state==='reviewed'&&!['queued','running'].includes(video.extraction?.status)?'':'disabled'}>抽帧</button><a class="small-action" href="/api/videos/${video.id}/annotations.json">标注</a><button class="small-action danger" data-action="delete">删除</button></div></article>`).join('');
      updateSelection();
    }catch(error){notice(error.message,true);}
  }
  function paused(){if(state.paused)throw new DOMException('上传已暂停','AbortError');}
  function digest(blob,onProgress=()=>{}) {
    paused();
    return new Promise((resolve,reject)=>{
      const worker=new Worker('/static/video-hash-worker.js');
      const done=(error,value)=>{worker.terminate();state.cancelTransfer=null;error?reject(error):resolve(value);};
      state.cancelTransfer=()=>done(new DOMException('上传已暂停','AbortError'));
      worker.onmessage=({data})=>{if(data.error)done(Error(data.error));else if(data.sha256)done(null,data.sha256);else if(data.progress!==undefined)onProgress(data.progress);};
      worker.onerror=()=>done(Error('无法读取视频摘要，请重新选择文件'));
      worker.postMessage(blob);
    });
  }
  async function uploadFile(file,index,total) {
    $('uploadName').textContent=`${index+1}/${total} · ${file.name}`;
    $('uploadStatus').textContent='读取视频摘要…';$('uploadProgress').value=0;
    const sha256=await digest(file,p=>{$('uploadStatus').textContent=`读取摘要 ${Math.floor(p*100)}%`;$('uploadProgress').value=p*100;});
    paused();
    const session=await api('/api/upload-sessions',{method:'POST',body:JSON.stringify({filename:file.name,size_bytes:file.size,sha256})});
    if(session.status==='succeeded')return session.result;
    let offset=session.offset;
    const progress=()=>{$('uploadProgress').value=offset/file.size*100;$('uploadStatus').textContent=`已保存 ${Math.floor(offset/file.size*100)}% · ${size(offset)} / ${size(file.size)}`;};
    progress();
    while(offset<file.size) {
      paused();
      const chunk=file.slice(offset,Math.min(file.size,offset+session.chunk_size));
      const chunkHash=await digest(chunk);
      let sent=false;
      for(let attempt=0;attempt<3&&!sent;attempt++) {
        paused();
        const controller=new AbortController();state.cancelTransfer=()=>controller.abort();
        try {
          const received=await api(`/api/upload-sessions/${session.session_id}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream','Upload-Offset':String(offset),'X-Chunk-Sha256':chunkHash},body:chunk,signal:controller.signal});
          offset=received.offset;sent=true;
        }catch(error) {
          if(state.paused||error.name==='AbortError')throw error;
          if(error.status&&error.status!==409&&error.status<500)throw error;
          // The server may have committed the chunk before the reply was lost.
          const received=await api(`/api/upload-sessions/${session.session_id}`);
          if(received.status==='succeeded')return received.result;
          if(received.offset!==offset){offset=received.offset;sent=true;}
          if(!sent&&attempt===2)throw error;
        }finally{state.cancelTransfer=null;}
      }
      progress();
    }
    paused();$('uploadStatus').textContent='校验完整视频并保存…';
    return api(`/api/upload-sessions/${session.session_id}/complete`,{method:'POST',body:'{}'});
  }
  async function upload(files) {
    if(state.uploading||!files.length)return;
    state.pendingFiles=files;state.uploading=true;state.paused=false;
    $('uploadButton').disabled=true;$('emptyUpload').disabled=true;$('uploadPanel').hidden=false;$('pauseUpload').textContent='暂停上传';
    const errors=[];let count=0;
    try {
      for(let index=0;index<files.length;index++) {
        try{await uploadFile(files[index],index,files.length);count++;}
        catch(error){if(error.name==='AbortError')break;errors.push(`${files[index].name}：${error.message}`);}
        if(state.paused)break;
      }
    }finally {
      state.uploading=false;state.cancelTransfer=null;$('uploadButton').disabled=false;$('emptyUpload').disabled=false;
      $('uploadPanel').hidden=!state.paused;$('pauseUpload').textContent='继续上传';$('uploadInput').value='';
    }
    notice(state.paused?'上传已暂停，已确认的分块保存在服务端。点击继续，或稍后重新选择同一文件。':`已导入 ${count} 个视频。${errors.length?' '+errors.join('；')+'。重新选择相同文件可续传。':''}`,!!errors.length);
    if(!state.paused)state.pendingFiles=[];
    state.offset=0;await load();
  }
  async function loadCapabilities() {
    try {
      const [data,settings]=await Promise.all([api('/api/capabilities'),api('/api/settings')]);state.analysis=data.analysis.ready;
      $('analysisState').textContent=data.analysis.ready?'可提交本机分析':'尚未配置本机模型';
      $('analysisHint').textContent=data.analysis.message;
      $('autoAnalyze').checked=settings.auto_analyze_uploads;$('autoAnalyze').disabled=false;
      $('storageLocation').textContent=`服务端视频保存位置：${data.storage.video_dir}（可通过启动参数 --data-dir 指定 Mac 本地磁盘目录）`;
      if(!data.ffprobe)notice('未找到 FFmpeg / ffprobe，请安装后再导入视频。',true);
    }catch(error){notice(error.message,true);}
  }
  async function jobs() {
    try {
      const data=await api('/api/jobs');
      $('jobs').innerHTML=data.jobs.map(job=>`<div class="job-row"><div class="job-detail"><strong>${esc(job.filename)}</strong><span class="${job.error?'job-error':'subtle'}">${esc(job.error||analysisLabels[job.status]||job.status)}${job.status==='running'?` · ${Math.round(job.progress*100)}%`:''}${job.summary?` · ${job.summary.segment_count} 个有效片段`:''}${job.origin==='upload'?' · 上传后自动分析':job.origin==='batch'?' · 批量分析':''}</span></div>${job.status==='succeeded'?`<button data-apply="${esc(job.id)}" data-video="${job.video_id}">查看并应用</button>`:''}${job.summary?`<a href="/api/jobs/${esc(job.id)}/result" class="small-action">下载 AI 结果</a>`:''}</div>`).join('');
      return data.jobs.some(job=>['queued','running'].includes(job.status));
    }catch(error){$('analysisState').textContent='任务状态暂时无法读取';return true;}
  }
  async function removeCases(ids) {
    const preview=await api('/api/cases/delete-preview',{method:'POST',body:JSON.stringify({video_ids:ids})});
    const names=preview.cases.slice(0,8).map(item=>item.filename).join('\n');
    if(!await confirmAction('整例删除',`将删除 ${preview.case_count} 个病例，包括原视频、${preview.annotation_count} 条标注、${preview.frame_count} 张已抽取帧、AI 结果及 ${preview.cached_export_count} 个服务端导出包。\n${names}${preview.case_count>8?'\n…':''}\n已下载的文件仍由你保管。此操作不可撤销，建议先导出。`,'删除所选病例'))return;
    await api('/api/cases/delete',{method:'POST',body:JSON.stringify({video_ids:ids,token:preview.token,confirm_count:preview.case_count})});
    ids.forEach(id=>state.selected.delete(id));notice(`已整例删除 ${ids.length} 个病例。`);await Promise.all([load(),jobs()]);
  }
  $('videoList').addEventListener('click',async event=>{
    const button=event.target.closest('[data-action]');if(!button)return;
    const video=state.videos.find(item=>item.id===Number(button.closest('[data-id]').dataset.id));button.disabled=true;
    try {
      if(button.dataset.action==='delete')await removeCases([video.id]);
      else if(button.dataset.action==='apply')await applyResult(video.analysis.id,video.id);
      else if(button.dataset.action==='extract') {
        const fps=Number($('frameFps').value);
        await api(`/api/videos/${video.id}/extract`,{method:'POST',body:JSON.stringify({fps,expected_revision:video.revision})});notice(`已提交 ${fps} fps 本机抽帧任务，仅抽取可判读有效阶段。`);await load();
      }else{await api(`/api/videos/${video.id}/analyze`,{method:'POST'});notice('本机分析任务已提交。完成后可核对并应用结果。');await Promise.all([load(),jobs()]);}
    }catch(error){notice(error.message,true);}finally{button.disabled=false;}
  });
  async function applyResult(jobId,videoId) {
    const [result,context]=await Promise.all([api(`/api/jobs/${jobId}/result`),api(`/clinical/api/videos/${videoId}/editor-context`)]);
    const segments=result.segments||[];
    const preview=segments.slice(0,8).map(item=>`${item.start_sec.toFixed(1)}–${item.end_sec.toFixed(1)} 秒`).join('、');
    if(!await confirmAction('应用 AI 阶段结果',`检测到 ${segments.length} 个有效片段${preview?'：\n'+preview:''}。\n将替换此视频现有的 ${context.rows.phase.length} 条阶段标注。结果仍需人工核对。`,'应用到阶段标注'))return;
    await api(`/api/jobs/${jobId}/apply`,{method:'POST',body:JSON.stringify({expected_revision:context.video.revision,replace_existing:true})});
    notice('AI 阶段结果已保存，请进入视频标注进行人工复核。');await Promise.all([load(),jobs()]);
  }
  $('jobs').addEventListener('click',async event=>{
    const button=event.target.closest('[data-apply]');if(!button)return;button.disabled=true;
    try{await applyResult(button.dataset.apply,button.dataset.video);}catch(error){notice(error.message,true);}finally{button.disabled=false;}
  });
  $('batchAnalyze').onclick=async()=>{
    const button=$('batchAnalyze');button.disabled=true;
    try {
      const result=await api('/api/analysis-batches',{method:'POST',body:JSON.stringify({video_ids:[...state.selected]})});
      const pending=result.items.filter(item=>item.state==='pending').length;
      const skipped=result.items.filter(item=>item.state==='skipped');
      notice(`已加入 ${pending} 个分析等待任务，跳过 ${skipped.length} 个。${!state.analysis?'模型就绪后自动开始。':''}${skipped.length?' '+skipped.map(item=>`病例 ${item.video_id}：${item.detail}`).join('；'):''}`);
      await load();
    }catch(error){notice(error.message,true);}finally{updateSelection();}
  };
  $('batchDelete').onclick=async()=>{const ids=[...state.selected];$('batchDelete').disabled=true;try{await removeCases(ids);}catch(error){notice(error.message,true);}finally{updateSelection();}};
  $('autoAnalyze').onchange=async event=>{const enabled=event.target.checked;event.target.disabled=true;try{await api('/api/settings',{method:'PATCH',body:JSON.stringify({auto_analyze_uploads:enabled})});notice(enabled?'后续上传完成后将自动分析；模型未就绪时保存等待任务。':'已关闭上传后自动分析；已提交的任务继续执行。');}catch(error){event.target.checked=!enabled;notice(error.message,true);}finally{event.target.disabled=false;}};
  $('pauseUpload').onclick=()=>{if(state.uploading){state.paused=true;state.cancelTransfer?.();}else upload(state.pendingFiles);};
  $('uploadButton').onclick=$('emptyUpload').onclick=()=>$('uploadInput').click();
  $('videoList').addEventListener('change',event=>{const checkbox=event.target.closest('[data-select]');if(checkbox){const id=Number(checkbox.dataset.select);checkbox.checked?state.selected.add(id):state.selected.delete(id);updateSelection();}});
  $('selectPage').onchange=event=>{state.videos.forEach(video=>event.target.checked?state.selected.add(video.id):state.selected.delete(video.id));load();};
  $('exportCases').onclick=async()=>{const button=$('exportCases');button.disabled=true;try{notice('正在校验并打包病例、原始图像帧和结构化报告…');const result=await api('/api/exports',{method:'POST',body:JSON.stringify({video_ids:[...state.selected]})});const link=document.createElement('a');link.href=result.download_url;link.download='';document.body.append(link);link.click();link.remove();notice(`已导出 ${result.case_count} 个病例、${result.frame_count} 张帧。ZIP 可直接交给原平台帧标注页导入。`);}catch(error){notice(error.message,true);}finally{updateSelection();}};
  $('uploadInput').onchange=event=>upload([...event.target.files]);
  let searchTimer;$('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.offset=0;load();},200);};
  $('previousPage').onclick=()=>{state.offset=Math.max(0,state.offset-state.limit);load();};
  $('nextPage').onclick=()=>{state.offset+=state.limit;load();};
  window.addEventListener('beforeunload',event=>{if(state.uploading){event.preventDefault();event.returnValue='';}});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){load();jobs();}});
  let polls=0;
  async function poll(){await jobs();await load();if(++polls%5===0)await loadCapabilities();setTimeout(poll,4000);}
  (async()=>{await loadCapabilities();await load();poll();})();
})();
