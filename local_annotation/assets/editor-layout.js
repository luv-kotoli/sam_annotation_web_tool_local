/* View-only controls: no annotation values, revisions or save requests. */
(() => {
  'use strict';
  const workspace = document.getElementById('segmentWorkspace');
  const upper = document.getElementById('annotationWorkspace');
  const panel = document.getElementById('annotationEditorDetails');
  const handle = document.getElementById('annotationResizeHandle');
  const toggle = document.getElementById('toggleAnnotationEditor');
  if (!workspace || !upper || !panel || !handle || !toggle) return;

  const desktop = matchMedia('(min-width:1250px)');
  let ratio = null;
  let drag = null;
  function limits() {
    const height = workspace.clientHeight;
    return {height, min: Math.min(120, height * .3), max: height - Math.min(140, height * .3)};
  }
  function render() {
    const {height, min, max} = limits();
    if (ratio === null || !desktop.matches || !height) {
      workspace.style.removeProperty('--annotation-upper-track');
    } else {
      const pixels = Math.max(min, Math.min(max, height * ratio));
      workspace.style.setProperty('--annotation-upper-track', `${pixels}px`);
    }
    if (!height) return;
    const percent = Math.round(upper.getBoundingClientRect().height / height * 100);
    handle.setAttribute('aria-valuemin', String(Math.round(min / height * 100)));
    handle.setAttribute('aria-valuemax', String(Math.round(max / height * 100)));
    handle.setAttribute('aria-valuenow', String(percent));
    handle.setAttribute('aria-valuetext', `上方信息区域 ${percent}%`);
  }
  function setHeight(pixels) {
    const {height, min, max} = limits();
    if (!height || !desktop.matches) return;
    ratio = Math.max(min, Math.min(max, pixels)) / height;
    render();
  }
  function syncToggle() {
    const label = panel.open ? '收起当前片段' : '展开当前片段';
    toggle.textContent = panel.open ? '−' : '+';
    toggle.setAttribute('aria-expanded', String(panel.open));
    toggle.setAttribute('aria-label', label);
    toggle.title = label;
    render();
  }
  // Native summary activation must never toggle the panel outside its button.
  const summary = panel.querySelector('summary');
  summary.addEventListener('click', event => event.preventDefault());
  summary.addEventListener('keydown', event => {
    if (event.target === summary && ['Enter', ' '].includes(event.key)) event.preventDefault();
  });
  toggle.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    panel.open = !panel.open;
    syncToggle();
  });
  panel.addEventListener('toggle', syncToggle);
  // Keep existing multiline diagnoses intact while accepting new text on one line.
  document.getElementById('diagnosisMain')?.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.isComposing) event.preventDefault();
  });

  function finishDrag() {
    if (!drag) return;
    const id = drag.id;
    drag = null;
    document.body.classList.remove('is-resizing-annotation');
    if (handle.hasPointerCapture(id)) handle.releasePointerCapture(id);
  }
  handle.addEventListener('pointerdown', event => {
    if (!desktop.matches || !event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    handle.focus({preventScroll: true});
    drag = {id: event.pointerId, y: event.clientY, height: upper.getBoundingClientRect().height};
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add('is-resizing-annotation');
  });
  handle.addEventListener('pointermove', event => {
    if (drag && event.pointerId === drag.id) setHeight(drag.height + event.clientY - drag.y);
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    handle.addEventListener(name, finishDrag);
  }
  window.addEventListener('blur', finishDrag);
  handle.addEventListener('dblclick', event => {
    event.preventDefault();
    ratio = null;
    render();
  });
  handle.addEventListener('keydown', event => {
    if (!desktop.matches) return;
    const {min, max} = limits();
    const current = upper.getBoundingClientRect().height;
    const step = event.shiftKey ? 24 : 8;
    const values = {ArrowUp: current - step, ArrowDown: current + step, Home: min, End: max};
    if (!(event.key in values)) return;
    event.preventDefault();
    event.stopPropagation();
    setHeight(values[event.key]);
  });
  desktop.addEventListener('change', () => { finishDrag(); render(); });
  new ResizeObserver(render).observe(workspace);
  syncToggle();
})();
