(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {videos:[], offset:0, limit:12, total:0, analysis:false, uploading:false, generation:0, selected:new Set()};
  function updateSelection(){ $('exportCases').disabled=!state.selected.size;$('exportCases').textContent=`导出所选病例包（${state.selected.size}）`;$('selectPage').checked=!!state.videos.length&&state.videos.every(video=>state.selected.has(video.id));}
  function extractionText(item){if(!item)return '尚未抽帧';const labels={queued:'等待抽帧',running:'正在抽帧',succeeded:`已抽取 ${item.frame_count} 帧`,stale:'标注已变化，需要重新抽帧',failed:'抽帧失败',interrupted:'抽帧已中断'};return (labels[item.status]||item.status)+(item.status==='running'?` ${Math.round(item.progress*100)}%`:'')+(item.error?'：'+item.error:'');}
  async function api(url, options={}) {
    const response = await fetch(url, {...options, headers:{'Content-Type':'application/json', ...options.headers}});
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : '操作失败，请重试');
    return data;
  }
  function notice(message, error=false) {$('notice').textContent=message;$('notice').hidden=!message;$('notice').classList.toggle('error',error);}
  function duration(value) {return `${Math.floor(value/60)}:${String(Math.floor(value%60)).padStart(2,'0')}`;}
  function size(value) {return value >= 1024**3 ? `${(value/1024**3).toFixed(1)} GB` : `${(value/1024**2).toFixed(1)} MB`;}
  async function confirmAction(title, message, label='确认') {
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
        <div class="video-content"><label class="case-selection"><input type="checkbox" data-select="${video.id}" ${state.selected.has(video.id)?'checked':''}>选择病例</label><h3 title="${esc(video.original_filename)}">${esc(video.original_filename)}</h3><span class="case-number">${esc(video.case_id)}</span><div class="video-meta"><span>${video.width} × ${video.height}</span><span>${size(video.size_bytes)}</span><span>${esc(video.codec)}</span></div><div class="video-status"><span class="state-badge ${video.review_state==='reviewed'?'reviewed':''}">${video.review_state==='reviewed'?'已完成复核':'待复核'}</span><span>${video.phase_count} 个阶段片段</span></div><div class="extraction-state">${esc(extractionText(video.extraction))}</div></div>
        <div class="video-actions"><a class="open-editor" href="${esc(video.editor_url)}">进入标注 ↗</a><button class="small-action" data-action="analyze" ${state.analysis?'':'disabled'} title="${state.analysis?'使用本机阶段模型分析视频':'阶段模型尚未就绪'}">AI 分期</button><button class="small-action" data-action="extract" ${video.review_state==='reviewed'&&!['queued','running'].includes(video.extraction?.status)?'':'disabled'}>抽帧</button><a class="small-action" href="/api/videos/${video.id}/annotations.json">标注</a><button class="small-action danger" data-action="delete">删除</button></div></article>`).join('');
      updateSelection();
    } catch(error) {notice(error.message,true);}
  }
  function uploadFile(file, index, total) {
    return new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();
      xhr.open('POST','/api/videos/upload?'+new URLSearchParams({filename:file.name}));
      xhr.setRequestHeader('Content-Type','application/octet-stream');
      $('uploadName').textContent=`${index+1}/${total} · ${file.name}`;
      xhr.upload.onprogress=event=>{if(event.lengthComputable){$('uploadProgress').value=event.loaded/event.total*100;$('uploadStatus').textContent=event.loaded===event.total?'校验视频…':`${Math.floor(event.loaded/event.total*100)}%`;}};
      xhr.onload=()=>{let data;try{data=JSON.parse(xhr.responseText);}catch(_){reject(Error('服务器返回格式异常'));return;}if(xhr.status===201)resolve();else reject(Error(`${file.name}：${data.detail||'导入失败'}`));};
      xhr.onerror=()=>reject(Error(`${file.name}：连接中断，请重新导入；已导入内容会自动查重。`));
      xhr.send(file);
    });
  }
  async function upload(files) {
    if(state.uploading||!files.length)return;
    state.uploading=true;$('uploadButton').disabled=true;$('emptyUpload').disabled=true;$('uploadPanel').hidden=false;
    const errors=[];let count=0;
    try {for(let index=0;index<files.length;index++){try{await uploadFile(files[index],index,files.length);count++;}catch(error){errors.push(error.message);}}}
    finally{state.uploading=false;$('uploadButton').disabled=false;$('emptyUpload').disabled=false;$('uploadPanel').hidden=true;$('uploadInput').value='';}
    notice(`已导入 ${count} 个视频。${errors.length?' '+errors.join('；'):''}`,!!errors.length);state.offset=0;await load();
  }
  async function loadCapabilities() {
    try {
      const data=await api('/api/capabilities');state.analysis=data.analysis.ready;
      $('analysisState').textContent=data.analysis.ready?'可提交本机分析':'尚未配置本机模型';
      $('analysisHint').textContent=data.analysis.message;
      $('storageLocation').textContent=`服务端视频保存位置：${data.storage.video_dir}（可通过启动参数 --data-dir 指定 Mac 本地磁盘目录）`;
      if(!data.ffprobe)notice('未找到 FFmpeg / ffprobe，请安装后再导入视频。',true);
    }catch(error){notice(error.message,true);}
  }
  async function jobs() {
    try {
      const data=await api('/api/jobs');
      const labels={queued:'等待分析',running:'正在分析',succeeded:'分析完成 · 待应用',failed:'分析失败',interrupted:'已中断，可重新分析',applied:'已应用 · 待人工复核'};
      $('jobs').innerHTML=data.jobs.map(job=>`<div class="job-row"><div class="job-detail"><strong>${esc(job.filename)}</strong><span class="${job.error?'job-error':'subtle'}">${esc(job.error||labels[job.status]||job.status)}${job.status==='running'?` · ${Math.round(job.progress*100)}%`:''}${job.summary?` · ${job.summary.segment_count} 个有效片段`:''}</span></div>${job.status==='succeeded'?`<button data-apply="${esc(job.id)}" data-video="${job.video_id}">查看并应用</button>`:''}${job.summary?`<a href="/api/jobs/${esc(job.id)}/result" class="small-action">下载 AI 结果</a>`:''}</div>`).join('');
      return data.jobs.some(job=>['queued','running'].includes(job.status));
    }catch(error){$('analysisState').textContent='任务状态暂时无法读取';return true;}
  }
  $('videoList').addEventListener('click',async event=>{
    const button=event.target.closest('[data-action]');if(!button)return;
    const video=state.videos.find(item=>item.id===Number(button.closest('[data-id]').dataset.id));
    button.disabled=true;
    try {
      if(button.dataset.action==='delete'){
        if(!await confirmAction('删除本机视频',`将删除 1 个视频及其阶段、征象和病例标注：\n${video.original_filename}\n建议先导出标注。此操作不可撤销。`,'删除视频'))return;
        await api(`/api/videos/${video.id}`,{method:'DELETE',body:JSON.stringify({expected_revision:video.revision,confirm_filename:video.original_filename})});state.selected.delete(video.id);notice('视频已从本机视频库删除。');await load();
      } else if(button.dataset.action==='extract') {
        const fps=Number($('frameFps').value);
        await api(`/api/videos/${video.id}/extract`,{method:'POST',body:JSON.stringify({fps,expected_revision:video.revision})});notice(`已提交 ${fps} fps 本机抽帧任务，仅抽取可判读有效阶段。`);await load();
      } else {await api(`/api/videos/${video.id}/analyze`,{method:'POST'});notice('本机分析任务已提交。完成后可核对并应用结果。');await jobs();}
    }catch(error){notice(error.message,true);}finally{button.disabled=false;}
  });
  $('jobs').addEventListener('click',async event=>{
    const button=event.target.closest('[data-apply]');if(!button)return;button.disabled=true;
    try {
      const [result,context]=await Promise.all([api(`/api/jobs/${button.dataset.apply}/result`),api(`/clinical/api/videos/${button.dataset.video}/editor-context`)]);
      const segments=result.segments||[];
      const preview=segments.slice(0,8).map(item=>`${item.start_sec.toFixed(1)}–${item.end_sec.toFixed(1)} 秒`).join('、');
      if(!await confirmAction('应用 AI 阶段结果',`检测到 ${segments.length} 个有效片段${preview?'：\n'+preview:''}。\n将替换此视频现有的 ${context.rows.phase.length} 条阶段标注。结果仍需人工核对。`,'应用到阶段标注'))return;
      await api(`/api/jobs/${button.dataset.apply}/apply`,{method:'POST',body:JSON.stringify({expected_revision:context.video.revision,replace_existing:true})});notice('AI 阶段结果已保存，请进入视频标注进行人工复核。');await Promise.all([load(),jobs()]);
    }catch(error){notice(error.message,true);}finally{button.disabled=false;}
  });
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
  async function poll(){await jobs();if(state.videos.some(video=>['queued','running'].includes(video.extraction?.status)))await load();setTimeout(poll,4000);}
  (async()=>{await loadCapabilities();await load();poll();})();
})();
