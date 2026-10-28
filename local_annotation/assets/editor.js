(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const { esc, svg } = window.WorkspaceShell;
  const state = {
    mode: document.body.dataset.mode || "phase",
    config: null,
    user: null,
    rows: { case: [], phase: [], finding: [] },
    selected: { case: null, phase: null, finding: null },
    choices: {},
    findingControls: {},
    ranges: {},
    dirty: new Set(),
    draftRows: {},
    loading: true,
    activeTable: "phase",
    timeline: null,
    workflowVideo: null,
    cropOverlay: null,
    videoContext: null,
    sourceBusy: false,
    sourceKind: "",
    clipIdAutoValue: "",
    previewing: false,
    saving: false,
    toastTimer: null,
    caseProfile: null,
    profileSaving: false,
    profileError: "",
    saveErrors: {},
    historyBusy: false,
    undoStack: [],
    redoStack: [],
    formBaseline: {},
    persistedForms: {},
    historySuspended: false,
    timelineEditing: false,
    phaseSavePromise: null,
    findingSavePromise: null,
    pageSaving: false,
    selectionBusy: false,
    videoNavigation: {},
    navigationPending: false,
    switchingVideo: false,
    stagingVideo: false,
    defaultFields: {},
    scrubResume: false,
    playheadFrame: null,
  };
  let PHASE_LABELS = [];

  const QUALITY_LEVELS = [
    ["0", "0 无效"],
    ["1", "1 低质量"],
    ["2", "2 可判读"],
    ["3", "3 高质量"],
  ];

  let PHASE_QUALITY_LEVELS = [];
  const phaseLabel = value => ['insertion','withdrawal'].includes(value) ? 'effective_observation' : value;
  const phaseQuality = value => ['2','3'].includes(String(value)) ? '2' : '1';

  let VISIBLE_SIDE_OPTIONS = [];

  const VISIBLE_REGION_OPTIONS = [
    ["septum", "鼻中隔"],
    ["inferior_turbinate", "下鼻甲"],
    ["middle_turbinate_or_meatus", "中鼻甲/中鼻道"],
    ["nasal_floor_or_common_meatus", "鼻底/总鼻道"],
    ["nasopharynx_or_choana", "鼻咽/后鼻孔"],
    ["postop_cavity", "术腔"],
  ];

  const FIELD_LABELS = {
    visible_septum: "鼻中隔",
    visible_inferior_turbinate: "下鼻甲",
    visible_middle_turbinate_or_meatus: "中鼻甲/鼻道",
    visible_nasal_floor_or_common_meatus: "鼻底/总鼻道",
    visible_nasopharynx_or_choana: "鼻咽/后鼻孔",
    visible_postop_cavity: "术腔可见",
    mucosa_normal: "正常",
    mucosal_swelling: "肿胀",
    mucosal_hyperemia: "充血",
    mucosal_pale_edema: "苍白水肿",
    mucosal_erosion: "糜烂破损",
    crust_or_scab: "鼻痂",
    secretion_present: "有无",
    secretion_type: "类型",
    secretion_amount: "分泌物量",
    turbinate_enlargement: "鼻甲肥大",
    turbinate_pale_edema: "鼻甲苍白水肿",
    nasal_meatus_narrowing: "鼻道狭窄",
    airway_obstruction_level: "通气阻塞",
    septal_deviation: "鼻中隔偏曲",
    septal_erosion: "鼻中隔糜烂",
    polyp_like_lesion: "息肉",
    bleeding: "出血/血迹",
    postoperative_change: "术后改变",
  };

  const FIELD_HELP = {
    visible_side:
      "当前有效观察片段主要显示左侧或右侧鼻腔；无法确定时保留不确定。",
    visible_regions:
      "当前阶段内能稳定看到的解剖区域，可多选。保存时用英文分号连接。",
    mucosal_swelling: "是否存在黏膜增厚或肿胀，可与其他黏膜状态同时勾选。",
    mucosal_hyperemia: "黏膜颜色偏红、血管充盈，提示充血。",
    mucosal_pale_edema: "黏膜苍白、水肿样，常见于过敏样改变。",
    secretion_type: "清亮、黏液样、脓性、血性分泌物的主要类型。",
    turbinate_enlargement: "鼻甲体积增大程度。",
    nasal_meatus_narrowing: "鼻道空间变窄，影响观察或通气。",
    septal_deviation: "鼻中隔偏向左侧、右侧，或偏曲方向不确定。",
    polyp_like_lesion: "当前有效片段是否存在息肉或息肉样改变。",
    postoperative_change: "术后术腔或术后结构改变；未涉及术后时选不适用。",
  };

  const BOOL_UNKNOWN = [
    ["0", "无/否"],
    ["1", "有/是"],
    ["unknown", "不确定"],
  ];

  const SEVERITY_012_UNKNOWN = [
    ["0", "无"],
    ["1", "轻度"],
    ["2", "中重度/明显"],
    ["unknown", "不确定"],
  ];

  const SEVERITY_01_UNKNOWN = [
    ["0", "无"],
    ["1", "有"],
    ["unknown", "不确定"],
  ];

  const SECRETION_TYPE_OPTIONS = [
    ["none", "无"],
    ["clear", "清亮"],
    ["mucoid", "黏液样"],
    ["purulent", "脓性"],
    ["bloody", "血性"],
    ["unknown", "不确定"],
  ];

  const SECRETION_AMOUNT_OPTIONS = [
    ["0", "无"],
    ["1", "少量"],
    ["2", "较多"],
    ["unknown", "不确定"],
  ];

  const SEPTAL_DEVIATION_OPTIONS = [
    ["0", "无"],
    ["1", "左偏"],
    ["2", "右偏"],
    ["3", "偏曲但方向不确定"],
    ["unknown", "不确定"],
  ];

  const POLYP_OPTIONS = [
    ["0", "无"],
    ["1", "疑似"],
    ["2", "明确"],
    ["unknown", "不确定"],
  ];

  const POSTOP_OPTIONS = [
    ["0", "无"],
    ["1", "有"],
    ["2", "不适用"],
  ];

  let PRE_OR_POST_OP_OPTIONS = [];

  const USABLE_FOR_MODEL_OPTIONS = [
    ["1", "建议纳入训练"],
    ["0", "不建议纳入训练"],
  ];

  let CASE_SEX_OPTIONS = [], FINDING_GROUPS = [], DIAGNOSIS_PRESETS = [];
  function configureCatalog(catalog) {
    if (!catalog?.case_fields || !catalog?.phase_fields || !catalog?.finding_groups)
      throw Error('标注词表未能加载，请刷新页面后重试');
    const options = (fields, key) => fields.find(field => field.key === key).options.map(item => [item.value, item.name]);
    CASE_SEX_OPTIONS = options(catalog.case_fields, 'case_sex');
    PRE_OR_POST_OP_OPTIONS = options(catalog.case_fields, 'pre_or_post_op');
    DIAGNOSIS_PRESETS = catalog.case_fields.find(field=>field.key==='diagnosis_main')?.options || [];
    PHASE_LABELS = options(catalog.phase_fields, 'phase_label');
    PHASE_QUALITY_LEVELS = options(catalog.phase_fields, 'phase_quality');
    VISIBLE_SIDE_OPTIONS = options(catalog.phase_fields, 'visible_side');
    FINDING_GROUPS = catalog.finding_groups.map(group => ({...group,
      controls: group.controls.map(control => {
        FIELD_LABELS[control.key] = control.title;
        return [control.type, control.key, control.options?.map(item => [item.value, item.name]), control.default];
      }),
    }));
  }

  class Timeline {
    constructor(canvas, callbacks) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.callbacks = callbacks;
      this.durationSec = 0;
      this.currentSec = 0;
      this.viewStartSec = 0;
      this.viewDurationSec = 60;
      this.selectionStartSec = 0;
      this.selectionEndSec = 0;
      this.segments = [];
      this.dragging = false;
      this.dragMode = null;
      this.dragStartSec = 0;
      this.pointerId = null;
      this.editing = false;
      this.minSelectionSec = 0.05;
      this.cssWidth = 800;
      this.cssHeight = 132;

      new ResizeObserver(() => this.resize()).observe(this.canvas);
      this.canvas.addEventListener("pointerdown", (event) => {
        if (this.onMouseDown(event) !== false)
          this.canvas.setPointerCapture(event.pointerId);
      });
      window.addEventListener("pointermove", (event) =>
        this.onMouseMove(event),
      );
      window.addEventListener("pointerup", (event) => this.onMouseUp(event));
      window.addEventListener("pointercancel", event => this.finishDrag(event, true));
      this.canvas.addEventListener("lostpointercapture", event => this.finishDrag(event, true));
      window.addEventListener("blur", () => this.finishDrag(null, true));
      this.canvas.addEventListener("wheel", (event) => this.onWheel(event), {
        passive: false,
      });
      this.resize();
    }

    resize() {
      const rect = this.canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      this.cssWidth = Math.max(1, rect.width || this.canvas.clientWidth || 800);
      this.cssHeight = Math.max(1, rect.height || 132);
      this.canvas.width = Math.round(this.cssWidth * ratio);
      this.canvas.height = Math.round(this.cssHeight * ratio);
      this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      this.draw();
    }

    setDuration(durationSec) {
      this.durationSec = Math.max(Number(durationSec) || 0, 0);
      if (this.durationSec > 0) {
        this.viewDurationSec = Math.min(Math.max(30, this.durationSec), 300);
      }
      this.viewStartSec = 0;
      this.clampView();
      this.draw();
    }

    setCurrent(sec) {
      this.currentSec = Math.max(Number(sec) || 0, 0);
      this.draw();
    }

    setSegments(segments) {
      this.segments = segments;
      this.draw();
    }

    setSelection(startSec, endSec, notify = true) {
      let start = Math.max(Number(startSec) || 0, 0);
      let end = Math.max(Number(endSec) || 0, 0);
      if (this.durationSec > 0) {
        start = Math.min(start, this.durationSec);
        end = Math.min(end, this.durationSec);
      }
      if (end < start) {
        [start, end] = [end, start];
      }
      this.selectionStartSec = start;
      this.selectionEndSec = end;
      this.ensureVisible(start, end);
      this.draw();
      if (notify) {
        this.callbacks.onSelectionChange(start, end);
      }
    }

    timelineRect() {
      return {
        x: 18,
        y: 26,
        width: Math.max(10, this.cssWidth - 36),
        height: 30,
      };
    }

    secondsToX(sec) {
      const rect = this.timelineRect();
      if (this.viewDurationSec <= 0) {
        return rect.x;
      }
      const ratio = (sec - this.viewStartSec) / this.viewDurationSec;
      return rect.x + ratio * rect.width;
    }

    xToSeconds(x) {
      const rect = this.timelineRect();
      const ratio = (x - rect.x) / Math.max(rect.width, 1);
      let sec = this.viewStartSec + ratio * this.viewDurationSec;
      if (this.durationSec > 0) {
        sec = Math.min(Math.max(sec, 0), this.durationSec);
      }
      return Math.max(sec, 0);
    }

    ensureVisible(startSec, endSec) {
      if (this.durationSec <= 0) {
        return;
      }
      if (startSec < this.viewStartSec) {
        this.viewStartSec = Math.max(0, startSec - this.viewDurationSec * 0.1);
      }
      if (endSec > this.viewStartSec + this.viewDurationSec) {
        this.viewStartSec = Math.min(
          Math.max(0, this.durationSec - this.viewDurationSec),
          endSec - this.viewDurationSec * 0.9,
        );
      }
      this.clampView();
    }

    clampView() {
      if (this.durationSec <= 0) {
        this.viewStartSec = 0;
        this.viewDurationSec = Math.max(this.viewDurationSec, 3);
        return;
      }
      this.viewDurationSec = Math.min(
        Math.max(this.viewDurationSec, 3),
        Math.max(this.durationSec, 3),
      );
      this.viewStartSec = Math.min(
        Math.max(this.viewStartSec, 0),
        Math.max(0, this.durationSec - this.viewDurationSec),
      );
    }

    onMouseDown(event) {
      if (event.button !== 0 || this.dragging || event.isPrimary === false) return false;
      const bounds = this.canvas.getBoundingClientRect();
      const x = event.clientX - bounds.left, y = event.clientY - bounds.top;
      const sec = this.xToSeconds(x);
      const playhead = this.hitTestPlayhead(x, y);
      if (playhead && !this.callbacks.canSeek()) return false;
      event.preventDefault();
      this.canvas.focus({preventScroll:true});
      this.dragging = true;
      this.pointerId = event.pointerId;
      this.dragStartSec = sec;
      this.dragStartX = event.clientX;
      this.dragMoved = false;
      this.editing = false;
      this.originalRange = [this.selectionStartSec, this.selectionEndSec];
      this.pendingSegment = this.hitTestSegment(x, y);
      if (playhead) {
        // The playhead is draggable only outside the segment strip.
        this.dragMode = 'seek';
        this.callbacks.onScrubStart();
        this.callbacks.onSeek(sec);
      } else {
        const handle = this.hitTestSelectionHandle(x, y);
        this.dragMode = handle ? 'resize-' + handle : this.hitTestSelectionBody(x,y) ? 'move' : 'select';
      }
      this.updateCursor(x,y);
    }

    updateCursor(x,y) {
      const playhead = this.hitTestPlayhead(x,y);
      const handle = this.hitTestSelectionHandle(x,y);
      const body = this.hitTestSelectionBody(x,y);
      this.canvas.style.cursor = this.dragging
        ? this.dragMode === 'seek' || this.dragMode.startsWith('resize-') ? 'ew-resize' : this.dragMode === 'move' ? 'grabbing' : 'crosshair'
        : playhead || handle ? 'ew-resize' : body ? 'grab' : this.hitTestSegment(x,y) ? 'pointer' : 'crosshair';
      this.canvas.title = playhead ? '拖动红线调整播放位置' : handle ? '拖动边缘调整选区范围'
        : body ? '拖动整个选区平移，长度保持不变' : '拖动框选片段，单击定位或选择标注';
    }

    onMouseMove(event) {
      const rect = this.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left, y = event.clientY - rect.top;
      if (!this.dragging) {
        if (x >= 0 && x <= rect.width && y >= 0 && y <= rect.height) this.updateCursor(x,y);
        return;
      }
      if (event.pointerId !== this.pointerId) return;
      const sec = this.xToSeconds(x);
      if (this.dragMode === 'seek') {
        this.callbacks.onSeek(sec);
        return;
      }
      if (!this.dragMoved) {
        if (Math.abs(event.clientX - this.dragStartX) < 5) return;
        this.dragMoved = true;
        if (!this.callbacks.canEdit()) return;
        this.editing = true;
        this.callbacks.onEditStart();
        if(this.dragMode.startsWith('resize-') && this.callbacks.canSeek()){
          this.boundaryScrub=true;
          this.callbacks.onScrubStart();
        }
      }
      if (!this.editing) return;
      if (this.dragMode === 'resize-start') {
        this.setSelection(Math.min(sec,this.originalRange[1]-this.minSelectionSec),this.originalRange[1]);
        if(this.callbacks.canSeek())this.callbacks.onSeek(this.selectionStartSec);
      } else if (this.dragMode === 'resize-end') {
        this.setSelection(this.originalRange[0],Math.max(sec,this.originalRange[0]+this.minSelectionSec));
        if(this.callbacks.canSeek())this.callbacks.onSeek(this.selectionEndSec);
      } else if (this.dragMode === 'move') {
        const length = this.originalRange[1] - this.originalRange[0];
        const maxStart = this.durationSec > 0 ? Math.max(0,this.durationSec-length) : Infinity;
        const start = Math.min(maxStart,Math.max(0,this.originalRange[0]+sec-this.dragStartSec));
        this.setSelection(start,start+length);
      } else {
        this.setSelection(Math.min(this.dragStartSec,sec),Math.max(this.dragStartSec,sec));
      }
    }

    onMouseUp(event) {
      if (event.button === 0) this.finishDrag(event);
    }

    finishDrag(event, cancelled=false) {
      if (!this.dragging || event && event.pointerId !== this.pointerId) return;
      if (!cancelled && event && (this.dragMoved || this.dragMode === 'seek')) this.onMouseMove(event);
      const mode=this.dragMode, editing=this.editing, pointer=this.pointerId;
      this.dragging=false;this.dragMode=null;this.pointerId=null;this.editing=false;
      try {
        if (mode === 'seek') {
          this.callbacks.onScrubEnd();
        } else if (editing) {
          if (mode === 'select' && this.selectionEndSec-this.selectionStartSec < .15) {
            const start=this.selectionStartSec;
            this.setSelection(start,Math.min(start+1,this.durationSec || start+1));
          }
          this.callbacks.onEditEnd();
        } else if (!cancelled && !this.dragMoved && event) {
          this.callbacks.onSeek(this.xToSeconds(event.clientX-this.canvas.getBoundingClientRect().left));
          if (this.pendingSegment && !this.callbacks.isSelected(this.pendingSegment)) this.callbacks.onSegmentSelect(this.pendingSegment);
        }
      } finally {
        if(this.boundaryScrub){this.boundaryScrub=false;this.callbacks.onScrubEnd();}
        if (this.canvas.hasPointerCapture(pointer)) this.canvas.releasePointerCapture(pointer);
        this.pendingSegment=null;
        if (event) {
          const bounds=this.canvas.getBoundingClientRect();
          this.updateCursor(event.clientX-bounds.left,event.clientY-bounds.top);
        }
      }
    }

    onWheel(event) {
      event.preventDefault();
      const delta = event.deltaY;
      if (delta === 0 || this.durationSec <= 0) {
        return;
      }
      const centerSec = this.xToSeconds(event.offsetX);
      if (event.ctrlKey) {
        const factor = delta < 0 ? 0.8 : 1.25;
        const newDuration = Math.min(
          Math.max(this.viewDurationSec * factor, 3),
          Math.max(this.durationSec, 3),
        );
        const ratio =
          (centerSec - this.viewStartSec) /
          Math.max(this.viewDurationSec, 0.001);
        this.viewStartSec = centerSec - ratio * newDuration;
        this.viewDurationSec = newDuration;
      } else {
        const pan = (delta / 100) * this.viewDurationSec * 0.12;
        this.viewStartSec += pan;
      }
      this.clampView();
      this.draw();
    }

    segmentBounds(segment, rect) {
      const start = Math.max(segment.startSec, this.viewStartSec);
      const end = Math.min(
        segment.endSec,
        this.viewStartSec + this.viewDurationSec,
      );
      if (end <= start) {
        return null;
      }
      const x1 = this.secondsToX(start);
      const x2 = this.secondsToX(end);
      const y =
        segment.kind === "finding" ? rect.y + rect.height - 12 : rect.y + 5;
      const height = segment.kind === "finding" ? 8 : rect.height - 10;
      return { x1, x2, y, height };
    }

    hitTestSegment(x, y) {
      const rect = this.timelineRect();
      for (let index = this.segments.length - 1; index >= 0; index -= 1) {
        const bounds = this.segmentBounds(this.segments[index], rect);
        if (!bounds) {
          continue;
        }
        if (
          x >= bounds.x1 &&
          x <= bounds.x2 &&
          y >= bounds.y - 3 &&
          y <= bounds.y + bounds.height + 3
        ) {
          return this.segments[index];
        }
      }
      return null;
    }

    hitTestSelectionHandle(x, y) {
      if (this.selectionEndSec <= this.selectionStartSec) {
        return null;
      }
      const rect = this.timelineRect();
      const y1 = rect.y + 2;
      const y2 = rect.y + rect.height - 2;
      if (y < y1 || y > y2) {
        return null;
      }
      const startX = this.secondsToX(this.selectionStartSec);
      const endX = this.secondsToX(this.selectionEndSec);
      const threshold = Math.min(8,Math.max(3,(endX-startX)/4));
      const startDistance=Math.abs(x-startX),endDistance=Math.abs(x-endX);
      if (Math.min(startDistance,endDistance) <= threshold)
        return startDistance <= endDistance ? 'start' : 'end';
      return null;
    }

    hitTestSelectionBody(x,y) {
      const rect=this.timelineRect();
      return this.selectionEndSec > this.selectionStartSec &&
        y >= rect.y+5 && y <= rect.y+rect.height-5 &&
        x >= this.secondsToX(this.selectionStartSec) && x <= this.secondsToX(this.selectionEndSec);
    }

    hitTestPlayhead(x,y) {
      const rect=this.timelineRect();
      if(y>=rect.y && y<=rect.y+rect.height)return false;
      const threshold=y < rect.y+5 ? 12 : 7;
      return this.currentSec >= this.viewStartSec && this.currentSec <= this.viewStartSec+this.viewDurationSec &&
        Math.abs(x-this.secondsToX(this.currentSec)) <= threshold && y >= rect.y-24 && y <= rect.y+rect.height+13;
    }

    draw() {
      const ctx = this.ctx;
      if (!ctx) {
        return;
      }
      ctx.clearRect(0, 0, this.cssWidth, this.cssHeight);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, this.cssWidth, this.cssHeight);
      const rect = this.timelineRect();
      roundedRect(ctx, rect.x, rect.y, rect.width, rect.height, 6);
      ctx.fillStyle = "#edf3f3";
      ctx.fill();
      ctx.strokeStyle = "#d7e3e5";
      ctx.lineWidth = 1;
      ctx.stroke();

      this.drawDefaultUnused(rect);
      this.drawTicks(rect);
      this.drawSegments(rect);
      this.drawSelection(rect);
      this.drawPlayhead(rect);

      const end = this.viewStartSec + this.viewDurationSec;
      const viewText = `视野 ${fmtSec(this.viewStartSec)}s - ${fmtSec(end)}s`;
      // Keep the viewport readout outside the canvas marker/handle lanes.
      const viewport = $("timelineViewport");
      if (viewport.textContent !== viewText) viewport.textContent = viewText;
    }

    drawDefaultUnused(rect) {
      const ctx = this.ctx;
      roundedRect(ctx, rect.x, rect.y + 5, rect.width, rect.height - 10, 4);
      ctx.fillStyle = "rgba(100, 116, 139, 0.18)";
      ctx.fill();
    }

    drawTicks(rect) {
      if (this.viewDurationSec <= 0) {
        return;
      }
      const ctx = this.ctx;
      const roughStep = this.viewDurationSec / 8;
      const candidates = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
      const step = candidates.reduce((best, item) =>
        Math.abs(item - roughStep) < Math.abs(best - roughStep) ? item : best,
      );
      let tick = Math.floor(this.viewStartSec / step) * step;
      ctx.strokeStyle = "#94a3b8";
      ctx.fillStyle = "#617184";
      ctx.lineWidth = 1;
      ctx.font = "11px sans-serif";
      while (tick <= this.viewStartSec + this.viewDurationSec + step) {
        const x = this.secondsToX(tick);
        if (x >= rect.x && x <= rect.x + rect.width) {
          ctx.beginPath();
          ctx.moveTo(x, rect.y - 6);
          ctx.lineTo(x, rect.y + rect.height + 6);
          ctx.stroke();
          ctx.fillText(`${fmtSec(tick)}s`, x + 2, rect.y + rect.height + 20);
        }
        tick += step;
      }
    }

    drawSegments(rect) {
      const ctx = this.ctx;
      const colors = {
        unused: ["rgba(100, 116, 139, 0.55)", "#475569"],
        insertion: ["rgba(107, 137, 191, 0.6)", "#5c7db4"],
        effective_observation: ["rgba(85, 151, 139, 0.6)", "#3b8276"],
        withdrawal: ["rgba(195, 155, 87, 0.6)", "#ac873f"],
        finding: ["rgba(172, 130, 164, 0.55)", "#916c8b"],
      };
      for (const segment of this.segments) {
        const bounds = this.segmentBounds(segment, rect);
        if (!bounds) {
          continue;
        }
        const color = colors[segment.label] ||
          colors[segment.kind] || ["rgba(124, 58, 237, 0.45)", "#6d28d9"];
        roundedRect(
          ctx,
          bounds.x1,
          bounds.y,
          Math.max(2, bounds.x2 - bounds.x1),
          bounds.height,
          4,
        );
        ctx.fillStyle = color[0];
        ctx.fill();
        ctx.strokeStyle = color[1];
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }

    drawSelection(rect) {
      if (this.selectionEndSec <= this.selectionStartSec) {
        return;
      }
      const ctx = this.ctx;
      const x1 = this.secondsToX(this.selectionStartSec);
      const x2 = this.secondsToX(this.selectionEndSec);
      roundedRect(
        ctx,
        x1,
        rect.y + 5,
        Math.max(2, x2 - x1),
        rect.height - 10,
        4,
      );
      ctx.fillStyle = "rgba(22, 111, 112, 0.12)";
      ctx.fill();
      ctx.strokeStyle = "#166f70";
      ctx.lineWidth = 2;
      ctx.stroke();
      this.drawSelectionHandle(ctx, x1, rect);
      this.drawSelectionHandle(ctx, x2, rect);
    }

    drawSelectionHandle(ctx, x, rect) {
      const handleWidth = 7;
      const handleY = rect.y + 5;
      const handleHeight = rect.height - 10;
      roundedRect(
        ctx,
        x - handleWidth / 2,
        handleY,
        handleWidth,
        handleHeight,
        3,
      );
      ctx.fillStyle = "#166f70";
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1;
      ctx.stroke();

    }

    drawPlayhead(rect) {
      if (
        this.currentSec < this.viewStartSec ||
        this.currentSec > this.viewStartSec + this.viewDurationSec
      ) {
        return;
      }
      const ctx = this.ctx;
      const x = this.secondsToX(this.currentSec);
      ctx.strokeStyle = "#dc2626";
      ctx.fillStyle = "#dc2626";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, rect.y - 18);
      ctx.lineTo(x, rect.y + rect.height + 10);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x, rect.y - 18, 4, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function roundedRect(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, Math.abs(width) / 2, Math.abs(height) / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + width - r, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + r);
    ctx.lineTo(x + width, y + height - r);
    ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
    ctx.lineTo(x + r, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  function fmtSec(value) {
    const num = Math.max(Number(value) || 0, 0);
    return num.toFixed(3).replace(/\.?0+$/, "") || "0";
  }

  function annotationDuration() {
    const registered = Number(state.workflowVideo?.duration_seconds);
    return registered > 0 && Number.isFinite(registered)
      ? registered : Number($("videoPlayer").duration) || 0;
  }

  function annotationSec(value) {
    const duration = annotationDuration();
    // Keep the readable millisecond display, but submit the actual endpoint.
    if (duration > 0 && Math.abs(value - duration) <= 0.0005 + 1e-9)
      return String(duration);
    return fmtSec(value);
  }

  function updateTimeLimits() {
    const duration = annotationDuration();
    state.timeline.setDuration(duration);
    $("startSec").max = $("endSec").max = fmtSec(duration);
  }

  function parseSec(value, fallback = 0) {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function paddedInt(value) {
    return String(Math.max(0, Math.floor(Number(value) || 0))).padStart(4, "0");
  }

  function today() {
    return (
      state.config?.annotationDate || new Date().toISOString().slice(0, 10)
    );
  }

  function randomHex8() {
    const bytes = new Uint8Array(4);
    window.crypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(
      "",
    );
  }

  function defaultClipId(start, end) {
    const base = $("videoId").value.trim() || "video";
    return `${base}_${paddedInt(start)}_${paddedInt(end)}`;
  }

  function selectedRange() {
    let start = parseSec($("startSec").value);
    let end = parseSec($("endSec").value);
    if (end < start) {
      [start, end] = [end, start];
    }
    return [start, end];
  }

  function syncSelectionInputs(start, end) {
    $("startSec").value = fmtSec(start);
    $("endSec").value = fmtSec(end);
    state.ranges[state.mode] = [start, end];
    $("rangeSummary").textContent =
      `${fmtSec(start)} – ${fmtSec(end)} 秒 · ${fmtSec(end - start)} 秒`;
    const nextClipId = defaultClipId(start, end);
    const clipInput = $("clipId");
    if (!clipInput.value.trim() || clipInput.value === state.clipIdAutoValue) {
      clipInput.value = nextClipId;
      state.clipIdAutoValue = nextClipId;
    }
    if (!state.loading) markDirty(state.mode);
  }

  function choiceName(name) {
    return `choice_${name}`;
  }

  function optionText(value, label) {
    return label;
  }

  function createChoiceGroup(
    containerOrId,
    name,
    options,
    defaultValue,
    onChange,
    kind,
  ) {
    const container =
      typeof containerOrId === "string" ? $(containerOrId) : containerOrId;
    if (!container) {
      return;
    }
    container.replaceChildren();
    if(kind==='select'){
      const select=document.createElement('select');select.name=choiceName(name);select.setAttribute('aria-label',({pre_or_post_op:'检查类型',overall_quality:'整体质量',usable_for_model:'纳入训练'})[name]||name);
      for(const [value,label] of options)select.add(new Option(label,value));
      select.value=defaultValue;state.choices[name]={value:defaultValue,select,inputs:[]};
      select.onchange=()=>{state.choices[name].value=select.value;onChange?.(select.value);};container.append(select);return;
    }
    const choiceCountClass =
      options.length <= 4
        ? `choice-count-${options.length}`
        : "choice-count-many";
    container.classList.remove(
      "choice-count-0",
      "choice-count-1",
      "choice-count-2",
      "choice-count-3",
      "choice-count-4",
      "choice-count-many",
    );
    container.classList.add("choice-tiles", choiceCountClass);
    state.choices[name] = {
      value: defaultValue ?? options[0]?.[0] ?? "",
      inputs: [],
    };
    for (const [value, label] of options) {
      const wrapper = document.createElement("label");
      wrapper.className = "choice-option";
      wrapper.title = optionText(value, label);
      const input = document.createElement("input");
      input.type = "radio";
      input.name = choiceName(name);
      input.value = value;
      input.checked = value === state.choices[name].value;
      input.addEventListener("change", () => {
        if (input.checked) {
          state.choices[name].value = value;
          if (onChange) {
            onChange(value);
          }
        }
      });
      const labelText = document.createElement("span");
      labelText.className = "choice-label";
      labelText.textContent = label;
      wrapper.append(input, labelText);
      container.append(wrapper);
      state.choices[name].inputs.push(input);
    }
  }

  function choiceValue(name) {
    const choice=state.choices[name];
    if(!choice)return '';
    return choice.select ? choice.select.value : choice.inputs.find(input=>input.checked)?.value ?? choice.value ?? '';
  }

  function setChoiceValue(name, value) {
    const choice = state.choices[name];
    if (!choice) {
      return;
    }
    const text = value == null ? "" : String(value);
    if (choice.select) {
      choice.select.value = text;
      choice.value = text;
      return;
    }
    const matched = choice.inputs.find((input) => input.value === text);
    if (matched) {
      matched.checked = true;
      choice.value = matched.value;
      matched.dispatchEvent(new Event("change"));
    } else {
      choice.inputs.forEach(input=>input.checked=false);
      choice.value=text;
    }
  }

  function createCheckOption(name, label, datasetKey) {
    const wrapper = document.createElement("label");
    wrapper.className = "check-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = name;
    input.dataset[datasetKey] = name;
    wrapper.append(input, document.createTextNode(label));
    return wrapper;
  }

  function helpIcon(name) {
    if (!FIELD_HELP[name]) {
      return null;
    }
    const span = document.createElement("span");
    span.className = "help";
    span.title = FIELD_HELP[name];
    span.textContent = "?";
    return span;
  }

  const COLUMN_NAMES = {
    finding_status: '征象',
    start_sec: "起点",
    end_sec: "终点",
    phase_label: "阶段",
    quality_level: "质量",
    clip_quality: "质量",
    notes: "备注",
    case_id: "病例",
    diagnosis_main: "诊断",
    pre_or_post_op: "检查类型",
    overall_quality: "整体质量",
    secretion_type: "分泌物",
  };
  const COLUMNS = {
    case: [
      "case_id",
      "diagnosis_main",
      "pre_or_post_op",
      "overall_quality",
    ],
    phase: ["start_sec", "end_sec", "phase_label", "quality_level", "finding_status"],
    finding: [
      "start_sec",
      "end_sec",
      "clip_quality",
      "secretion_type",
    ],
  };

  async function apiJson(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        ...(options.headers || {}),
      },
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) {
      location.href =
        "/login?next=" +
        encodeURIComponent(location.pathname + location.search);
      throw Error("登录已过期，请重新登录");
    }
    if (!response.ok) {
      const error = Error(typeof data.detail === "string" ? data.detail : data.error || `请求失败 (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function showToast(message, type = "") {
    $("toast").textContent = message;
    $("toast").className = "toast show" + (type === "error" ? " error" : "");
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(
      () => $("toast").classList.remove("show"),
      4000,
    );
  }
  function notice(message, error = false) {
    $("notice").hidden = !message;
    $("notice").textContent = message;
    $("notice").classList.toggle("error", error);
  }
  const clone = (value) => JSON.parse(JSON.stringify(value));
  function formControls(table) {
    const sections = table === "case" ? ["#caseDetails"] : [`#${table}PrimaryEditor`, `#${table}Editor`];
    return [...document.querySelectorAll(sections.flatMap(section => ["input", "select", "textarea"].map(tag => `${section} ${tag}`)).join(","))].filter(input=>input.id!=="caseId");
  }
  function captureForm(table) {
    return {
      fields: formControls(table).map((input) => ({value: input.value, checked: input.checked, indeterminate:input.indeterminate})),
      range: table === state.mode ? selectedRange() : clone(state.ranges[table] || [0, 0]),
      batch: table === state.mode ? $("batchId").value : (state.batches?.[table] || "batch_v1"),
      selected: state.selected[table] ? clone(state.selected[table]) : null,
      dirty: state.dirty.has(table),
      clipIdAutoValue: state.clipIdAutoValue,
      notes: table==='case' ? undefined : $('annotationNotes').value,
      expectedRows: state.draftRows[table],
    };
  }
  function recordDraft(table) {
    if (state.loading || state.historySuspended || state.timelineEditing || table === "case") return;
    const after = captureForm(table), before = state.formBaseline[table];
    if (before && JSON.stringify(before) !== JSON.stringify(after)) {
      const target = document.activeElement;
      const key = target?.id || target?.name || "selection";
      const previous = state.undoStack.at(-1);
      const now = Date.now();
      if (previous?.kind === "draft" && previous.table === table && previous.key === key && now - previous.time < 900) {
        previous.after = clone(after); previous.time = now;
      } else {
        state.undoStack.push({kind: "draft", table, before: clone(before), after: clone(after), key, time: now});
      }
      state.redoStack = [];
      if (state.undoStack.length > 80) state.undoStack.shift();
    }
    state.formBaseline[table] = clone(after);
    updateHistoryButtons();
  }
  function restoreForm(table, form) {
    state.historySuspended = true;
    state.loading = true;
    if (table !== "case") setMode("phase",false);
    formControls(table).forEach((input, index) => {
      const field = form.fields[index];
      if (field) {input.value = field.value; input.checked = field.checked; input.indeterminate=!!field.indeterminate;}
    });
    for (const choice of Object.values(state.choices)) {
      if (choice.select) choice.value = choice.select.value;
      else choice.value = choice.inputs.find((input) => input.checked)?.value || "";
    }
    const selected = form.selected;
    state.selected[table] = selected ? state.rows[table].find((item) => item.index === selected.index) ||
      state.rows[table].find((item) => JSON.stringify(item.row) === JSON.stringify(selected.row)) || null : null;
    state.ranges[table] = clone(form.range);
    state.batches ||= {};
    state.batches[table] = form.batch;
    if (table !== "case") {
      $("batchId").value = form.batch;
      state.timeline.setSelection(...form.range);
      if (table === "finding") state.clipIdAutoValue = form.clipIdAutoValue;
    }
    form.dirty ? state.dirty.add(table) : state.dirty.delete(table);
    if(form.dirty)state.draftRows[table]=clone(form.expectedRows || state.rows[table]);
    else delete state.draftRows[table];
    if(table!=='case')$('annotationNotes').value=form.notes || '';
    updatePhaseConditionalUI();
    state.loading = false;
    state.historySuspended = false;
    state.formBaseline[table] = captureForm(table);
    for(const kind of ['phase','finding'])renderTable(kind);
    switchTable(table);
    updateSaveState();
  }
  function updateHistoryButtons() {
    const busy = state.saving || state.pageSaving || state.historyBusy || state.sourceBusy || !clinicalEditable();
    $("undoAnnotation").disabled = busy || !state.undoStack.length;
    $("redoAnnotation").disabled = busy || !state.redoStack.length;
    const labels = {phase: "阶段", finding: "征象", segment: "片段"};
    for (const [id, stack, verb, shortcut] of [["undoAnnotation", state.undoStack, "撤销", "Ctrl+Z"], ["redoAnnotation", state.redoStack, "重做", "Ctrl+Shift+Z"]]) {
      const item = stack.at(-1);
      $(id).title = item ? `${verb}${item.kind === "saved" ? "已保存的" : ""}${labels[item.table]}${item.action || "编辑"}（${shortcut}）` : `${verb}（${shortcut}）`;
    }
  }
  function rememberSavedChange(table, beforeRows, beforeForm, action, keepDrafts=false) {
    if (table === "case") return;
    if(!keepDrafts)state.undoStack = state.undoStack.filter((item) => !(item.kind === "draft" && item.table === table));
    const after = captureForm(table);
    state.undoStack.push({kind: "saved", table, beforeRows: clone(beforeRows), afterRows: clone(state.rows[table]), before: clone(beforeForm), after: clone(after), action});
    state.redoStack = [];
    state.formBaseline[table] = clone(after);
    state.persistedForms[table] = clone(after);
    updateHistoryButtons();
  }
  async function travelHistory(direction) {
    if (!clinicalEditable() || state.saving || state.pageSaving || state.historyBusy || state.sourceBusy) return;
    const from = direction === "undo" ? state.undoStack : state.redoStack;
    const to = direction === "undo" ? state.redoStack : state.undoStack;
    const command = from.at(-1);
    if (!command) return;
    if(command.table==='segment')return travelSegmentHistory(direction,command,from,to);
    const undo = direction === "undo", table = command.table;
    if (command.kind === "draft") {
      restoreForm(table, undo ? command.before : command.after);
      from.pop(); to.push(command); updateHistoryButtons(); return;
    }
    if (state.dirty.has(table)) {
      showToast("此页签还有未保存的输入，请先保存或撤销草稿后再恢复已保存记录。", "error"); return;
    }
    state.historyBusy = true;
    updateSaveState();
    try {
      const payload = await apiJson("/clinical/api/annotation-history", {
        method: "POST", body: JSON.stringify({case_id: $("caseId").value, video_id: $("videoId").value, table,
          expected_rows: undo ? command.afterRows : command.beforeRows,
          restore_rows: undo ? command.beforeRows : command.afterRows}),
      });
      state.rows[table] = payload.rows || [];
      if (undo) command.beforeRows = clone(state.rows[table]);
      else command.afterRows = clone(state.rows[table]);
      restoreForm(table, undo ? command.before : command.after);
      state.dirty.delete(table);
      delete state.saveErrors[table];
      state.formBaseline[table] = captureForm(table);
      state.persistedForms[table] = captureForm(table);
      refreshTimelineSegments();
      await refreshVideoContext().catch(() => notice("标注已恢复，视频状态刷新失败，请稍后重新加载。", true));
      from.pop(); to.push(command);
      // Restoring may assign new row IDs. Rebase the next adjacent saved action only
      // when its expected row contents are exactly the state just restored.
      rebaseNextHistory(from,undo,[table]);
      showToast((undo ? "已撤销" : "已重做") + "保存的标注；复核状态需重新确认");
    } catch (error) {
      notice(error.status === 409 ? "其他操作已更新这些标注，无法安全撤销或重做。当前输入已保留，请刷新核对。" : "恢复标注失败：" + error.message, true);
      showToast(error.status === 409 ? "其他操作已更新这些标注，无法安全撤销或重做。当前输入已保留，请刷新核对。" : "恢复标注失败：" + error.message, "error");
    } finally {state.historyBusy = false;updateSaveState();}
  }

  function segmentSnapshot() {return clone({phase:state.rows.phase,finding:state.rows.finding});}
  function rebaseNextHistory(stack,undo,tables) {
    const next=stack.at(-1),key=undo?'afterRows':'beforeRows';
    if(next?.kind!=='saved')return;
    const rowsKey=rows=>JSON.stringify(rows.map(item=>item.row).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b))));
    if(next.table==='segment') {
      for(const table of tables)if(rowsKey(next[key][table])===rowsKey(state.rows[table]))next[key][table]=clone(state.rows[table]);
    } else if(tables.includes(next.table) && rowsKey(next[key])===rowsKey(state.rows[next.table]))next[key]=clone(state.rows[next.table]);
  }
  async function travelSegmentHistory(direction,command,from,to) {
    if(state.dirty.has('phase')||state.dirty.has('finding')){showToast('请先保存或撤销当前草稿，再恢复已保存片段。','error');return;}
    const undo=direction==='undo';state.historyBusy=true;updateSaveState();
    try {
      const payload=await apiJson('/clinical/api/annotation-history',{method:'POST',body:JSON.stringify({
        case_id:$('caseId').value,video_id:$('videoId').value,table:'segment',
        expected_rows:undo?command.afterRows:command.beforeRows,restore_rows:undo?command.beforeRows:command.afterRows})});
      Object.assign(state.rows,payload.rows);
      command[undo?'beforeRows':'afterRows']=segmentSnapshot();
      for(const table of ['phase','finding']) {
        restoreForm(table,(undo?command.before:command.after)[table]);
        state.dirty.delete(table);delete state.draftRows[table];delete state.saveErrors[table];
        state.formBaseline[table]=captureForm(table);state.persistedForms[table]=captureForm(table);
      }
      refreshTimelineSegments();
      await refreshVideoContext().catch(()=>notice('片段已恢复，视频状态刷新失败。',true));
      from.pop();to.push(command);rebaseNextHistory(from,undo,['phase','finding']);
      showToast(undo?'已恢复整段阶段和征象标注':'已重新删除整段阶段和征象标注');
    } catch(error) {showToast('恢复片段失败：'+error.message,'error');}
    finally {state.historyBusy=false;updateSaveState();}
  }

  function markDirty(section) {
    if (state.loading) return;
    if(!state.dirty.has(section))state.draftRows[section]=clone(state.rows[section]);
    state.dirty.add(section);
    recordDraft(section);
    updateSaveState();
  }
  async function flushPhase(){
    if(state.phaseSavePromise){await state.phaseSavePromise;return !state.dirty.has('phase');}
    if(!state.dirty.has('phase'))return true;
    if(state.timelineEditing||state.historyBusy||state.saving)return false;
    state.phaseSavePromise=saveChanges('phase',{automatic:true});
    try{return await state.phaseSavePromise;}
    finally{state.phaseSavePromise=null;}
  }
  function selectedEffectivePhase() {
    const selected=state.selected.phase;
    if(selected && phaseLabel(selected.row.phase_label)==='effective_observation')return selected;
    return null;
  }
  function updateFindingConditionalUI() {
    const effective=choiceValue('phase_label')==='effective_observation';
    const blocked=!clinicalEditable() || !effective || state.saving || state.pageSaving || state.historyBusy;
    if($('findingFieldset'))$('findingFieldset').disabled=blocked;
    $('findingScopeHint').hidden=effective;
    $('findingScopeHint').textContent=effective?'':'请选择有效观察片段后标注征象';
  }
  async function flushFinding() {
    if(state.findingSavePromise){await state.findingSavePromise;return !state.dirty.has('finding');}
    if(!state.dirty.has('finding'))return true;
    if(state.timelineEditing || state.historyBusy || state.saving)return false;
    state.findingSavePromise=saveChanges('finding',{automatic:true});
    try{return await state.findingSavePromise;}
    finally{state.findingSavePromise=null;}
  }
  async function flushAnnotations() {
    if(state.findingSavePromise)await state.findingSavePromise;
    if(state.phaseSavePromise)await state.phaseSavePromise;
    if(state.findingSavePromise)await state.findingSavePromise;
    // Persist the phase first, so a new finding can bind to its saved revision.
    if(!await flushPhase())return false;
    return await flushFinding();
  }
  async function savePageAnnotations() {
    if(state.saving || state.pageSaving || state.historyBusy || state.sourceBusy || !clinicalEditable())return false;
    state.pageSaving=true;updateSaveState();
    try {
      if(state.dirty.has('finding') && choiceValue('phase_label')!=='effective_observation'){
        showToast('有未保存的征象，请先选择有效观察或撤销征象修改。','error');return false;
      }
      // A finding-only draft on a new selection still needs a persisted phase.
      if(state.dirty.has('finding')&&!state.selected.phase)markDirty('phase');
      if(state.dirty.has('phase') && state.selected.finding && !state.dirty.has('finding') && choiceValue('phase_label')==='effective_observation')markDirty('finding');
      if(!await flushAnnotations())return false;
      if((state.dirty.has('case')||caseProfileDirty())&&!await saveCaseOverview())return false;
      showToast('当前页面标注已保存');return true;
    } finally {state.pageSaving=false;updateSaveState();}
  }

  function updateSaveState(saved = false) {
    const dirty=state.dirty.has('phase') || state.dirty.has('finding');
    const error=state.saveErrors.phase || state.saveErrors.finding;
    $('saveState').textContent=state.saving || state.pageSaving || state.historyBusy ? (state.historyBusy?'正在恢复标注…':'正在保存…')
      : error || (dirty?'有未保存的修改 · Ctrl+S 保存':'标注已保存');
    $('saveState').className='save-state'+(error?' error':dirty?' dirty':' saved');
    $("caseSaveState").textContent = state.saveErrors.case ? state.saveErrors.case : state.dirty.has("case")
      ? "未保存的修改"
      : state.selected.case
        ? "已保存"
        : "未保存";
    $("caseSaveState").classList.toggle("dirty", state.dirty.has("case"));
    for (const button of document.querySelectorAll("[data-mode-tab]")) {
      button.classList.toggle("has-changes", state.dirty.has(button.dataset.modeTab));
      button.disabled = state.saving || state.historyBusy;
    }
    $("selectionTitle").textContent = state.selected[state.mode]
      ? "编辑已保存的片段"
      : "新选段";
    const blocked = !clinicalEditable() || state.saving || state.pageSaving || state.historyBusy;
    document.querySelectorAll('[data-diagnosis]').forEach(button=>button.disabled=blocked);
    for (const input of document.querySelectorAll(".annotation-panel input,.annotation-panel select,.annotation-panel textarea,#caseDetails input,#caseDetails select,#caseDetails textarea,#batchId,#startSec,#endSec,#newSegment")) {
      if(!input.readOnly&&!input.dataset.profile)input.disabled = blocked;
    }
    document.querySelectorAll('[data-delete-row],[data-delete-segment]').forEach(button=>button.disabled=blocked||state.sourceBusy);
    $('retryPhaseSave').hidden = !error || !dirty;
    $('retryPhaseSave').disabled = blocked || state.sourceBusy;
    $("saveCase").disabled = state.saving || state.pageSaving || state.profileSaving || state.sourceBusy || !state.caseProfile || isLegacyLocked();
    $("reviewButton").disabled = blocked || state.sourceBusy;
    $('savePageAnnotations').disabled=blocked || state.sourceBusy;
    $('annotationNotes').disabled=blocked;
    updateHistoryButtons();
    updateVideoControls();
    updateCaseProfileState();
    updatePhaseConditionalUI();
    updateFindingConditionalUI();
  }
  async function allowDiscard(sections = [state.mode], {preserve=false}={}) {
    if(state.pageSaving || state.saving || state.historyBusy)return false;
    if (!sections.some((section) => section === "profile" ? caseProfileDirty() : state.dirty.has(section))) return true;
    if (!await confirmDialog("当前有未保存的修改，放弃这些修改并继续？"))
      return false;
    if(preserve)return true;
    for (const section of sections) {state.dirty.delete(section);delete state.draftRows[section];}
    if(sections.includes('profile')){
      setProfileFields(state.caseProfile);
    }
    updateSaveState();
    return true;
  }
  function setupControls() {
    const expanded = window.matchMedia("(min-width: 1200px)").matches;
    createChoiceGroup('caseSexChoices','case_sex',CASE_SEX_OPTIONS,'',updateCaseProfileState);
    state.choices.case_sex.inputs.forEach(input=>input.dataset.profile='sex');
    createChoiceGroup('preOrPostOpChoices','pre_or_post_op',PRE_OR_POST_OP_OPTIONS,'routine');
    $('caseDiagnosisChoices').innerHTML=DIAGNOSIS_PRESETS.map(item=>`<button type="button" data-diagnosis="${esc(item.value)}">${esc(item.name)}</button>`).join('');
    createChoiceGroup(
      "phaseButtons",
      "phase_label",
      PHASE_LABELS,
      "effective_observation",
      updatePhaseConditionalUI,
    );
    createChoiceGroup(
      "phaseQualityChoices",
      "phase_quality",
      PHASE_QUALITY_LEVELS,
      "2",
      updatePhaseConditionalUI,
    );
    createChoiceGroup(
      "visibleSideChoices",
      "visible_side",
      VISIBLE_SIDE_OPTIONS,
      "unknown",
    );
    FINDING_GROUPS.forEach(group=>{
      const section=document.createElement('section');section.className='finding-group finding-'+group.layout;section.dataset.group=group.id;
      const heading=document.createElement('h3');heading.textContent=group.title;
      const list=document.createElement('div');list.className='finding-option-grid';
      for(const [type,name,options,defaultValue] of group.controls){
        if(type==='checkbox'){
          const label=createCheckOption(name,FIELD_LABELS[name],'findingField');
          const input=label.querySelector('input');input.name=choiceName(name);input.checked=!!defaultValue;
          label.classList.add('finding-check');list.append(label);
          state.findingControls[name]={type:'checkbox',element:input};
        }else{
          const field=document.createElement('div');field.className='finding-field';field.id='finding-'+name;
          const title=document.createElement('span');title.className='finding-field-title';title.textContent=FIELD_LABELS[name];
          const choices=document.createElement('div');choices.className='choice-grid';
          createChoiceGroup(choices,name,options,defaultValue,updateFindingConditionalUI);
          field.append(title,choices);list.append(field);state.findingControls[name]={type:'radio',name};
        }
      }
      section.append(heading,list);$('findingControls').append(section);
    });
    updateFindingConditionalUI();
    updatePhaseConditionalUI();
  }
  function updatePhaseConditionalUI() {
    const effective = choiceValue("phase_label") === "effective_observation";
    const disabled=!effective || !clinicalEditable() || state.saving || state.pageSaving || state.historyBusy;
    for(const [block,name] of [['phaseQualityBlock','phase_quality'],['visibleSideBlock','visible_side']]) {
      $(block).classList.toggle('is-inactive',!effective);
      for(const input of state.choices[name]?.inputs || [])input.disabled=disabled;
    }
  }
  function setMode(mode, updateUrl = true) {
    if (!["phase", "finding"].includes(mode)) return;
    state.ranges[state.mode] = selectedRange();
    state.batches ||= {};
    state.batches[state.mode] = $("batchId").value;
    state.mode = mode;
    $("batchId").value = state.batches[mode] || state.selected[mode]?.row.annotation_batch_id || "batch_v1";
    document.body.dataset.mode = mode;
    $("phaseEditor").hidden = false;
    $("findingEditor").hidden = false;
    document.querySelectorAll("[data-mode-tab]").forEach((button) => {
      button.classList.toggle("active", button.dataset.modeTab === mode);
      button.setAttribute(
        "aria-selected",
        String(button.dataset.modeTab === mode),
      );
    });
    switchTable(mode);
    updateSaveState();
    if (updateUrl) {
      const url = new URL(location.href);
      url.searchParams.set("mode", mode);
      history.replaceState(null, "", url);
    }
  }
  function tableElement(table) {
    return $(table + "Table");
  }
  function switchTable(table) {
    if(table==='case')return;
    state.activeTable='phase';$('phaseTable').classList.add('active');$('findingTable').classList.remove('active');
  }
  function displayValue(column, value, row = {}) {
    if(column==='finding_status')return phaseLabel(row.phase_label)==='effective_observation' ? (matchingRecord('finding',row)?'已标注':'待标注') : '—';
    if (column === 'quality_level') {
      if (row.phase_label === 'unused') value='';
      else if (['insertion','withdrawal'].includes(row.phase_label) && !value) value='1';
    }
    if (value === undefined || value === "") return "—";
    if (column === "phase_label")
      return PHASE_LABELS.find((option) => option[0] === phaseLabel(value))?.[1] || value;
    if (column === 'quality_level' && ['0','1','2','3'].includes(String(value)))
      return PHASE_QUALITY_LEVELS.find(option=>option[0]===phaseQuality(value))?.[1] || value;
    if (
      column === "quality_level" ||
      column === "clip_quality" ||
      column === "overall_quality"
    )
      return QUALITY_LEVELS.find((option) => option[0] === value)?.[1] || value;
    if (column === "pre_or_post_op")
      return (
        PRE_OR_POST_OP_OPTIONS.find((option) => option[0] === value)?.[1] ||
        value
      );
    if (column === "secretion_type")
      return (
        SECRETION_TYPE_OPTIONS.find((option) => option[0] === value)?.[1] ||
        value
      );
    return String(value);
  }
  function renderTable(table) {
    if(table !== 'phase') return;
    const columns = COLUMNS[table],
      rows = segmentRows(table);
    tableElement(table).innerHTML =
      "<thead><tr>" +
      columns
        .map((column) => "<th>" + COLUMN_NAMES[column] + "</th>")
        .join("") + '<th class="record-actions" scope="col">操作</th>' +
      "</tr></thead><tbody>" +
      (rows.length
        ? rows
            .map(
              (item) =>
                '<tr data-index="' + item.index + '" data-source-table="' + (item.sourceTable || table) + '" tabindex="0" class="' +
                (sameRange(item.row, {start_sec: selectedRange()[0], end_sec: selectedRange()[1]})
                  ? "selected"
                  : "") +
                '">' +
                columns
                  .map(
                    (column) =>
                      '<td title="' +
                      esc(segmentCell(item,column)) +
                      '">' +
                      esc(segmentCell(item,column)) +
                      "</td>",
                  )
                  .join("") + `<td class="record-actions"><button type="button" class="record-delete" data-delete-segment="${item.sourceTable||table}" data-row-index="${item.index}" aria-label="删除片段标注" title="删除本片段的阶段和征象标注（可撤销）" ${!clinicalEditable()||state.saving||state.pageSaving||state.historyBusy||state.sourceBusy?'disabled':''}>${svg('trash')}</button></td>` +
                "</tr>",
            )
            .join("")
        : '<tr><td colspan="' +
          (columns.length+1) +
          '" class="empty">还没有保存的记录</td></tr>') +
      "</tbody>";
    $('recordCount').textContent = rows.length;
  }
  function segmentCell(item,column) {
    if(column==='finding_status')return item.records.finding.length?'已标注':phaseLabel(item.row.phase_label)==='effective_observation'?'待标注':'—';
    return displayValue(column,item.row[column],item.row);
  }
  function rangeKey(row) {
    return JSON.stringify([parseSec(row.start_sec),parseSec(row.end_sec)]);
  }
  function sameRange(left,right) {return rangeKey(left) === rangeKey(right);}
  function matchingRecord(table,row) {
    if(table==='finding' && row.segment_id){
      const bound=state.rows.finding.find(item=>item.row.phase_segment_id===row.segment_id);
      if(bound)return bound;
    }
    const matches=state.rows[table].filter(item=>sameRange(item.row,row));
    return matches.find(item=>item.index===state.selected[table]?.index)||matches[0];
  }
  function segmentRows(table) {
    const rows=state.rows.phase.map(item=>({...item,records:{phase:[item],finding:[]}}));
    for(const item of state.rows.finding) {
      // A saved binding takes priority over timestamps, including after a phase
      // range was edited. Unbound legacy findings match the exact interval.
      const bound=item.row.phase_segment_id;
      const phase=rows.find(group=>group.records.phase.length && (bound
        ?group.row.segment_id===bound:sameRange(group.row,item.row)));
      if(phase){phase.records.finding.push(item);continue;}
      const orphan=rows.find(group=>!group.records.phase.length && sameRange(group.row,item.row));
      if(orphan){orphan.records.finding.push(item);continue;}
      rows.push({index:item.index,sourceTable:'finding',placeholder:true,records:{phase:[],finding:[item]},row:{
        start_sec:item.row.start_sec,end_sec:item.row.end_sec,phase_label:'未标注阶段',quality_level:'—',
      }});
    }
    return rows.sort((a,b)=>parseSec(a.row.start_sec)-parseSec(b.row.start_sec)||parseSec(a.row.end_sec)-parseSec(b.row.end_sec)||a.index-b.index);
  }
  async function refreshAllTables() {
    if (!$("caseId").value.trim()) {
      for (const table of ["case", "phase", "finding"]) {
        state.rows[table] = [];
        renderTable(table);
      }
      return;
    }
    const params = new URLSearchParams({ case_id: $("caseId").value.trim() });
    await Promise.all(
      ["case", "phase", "finding"].map(async (table) => {
        const query = new URLSearchParams(params);
        query.set("table", table);
        if (table !== "case" && $("videoId").value.trim())
          query.set("video_id", $("videoId").value.trim());
        state.rows[table] =
          (await apiJson("/clinical/api/rows?" + query)).rows || [];
        if (state.selected[table])
          state.selected[table] =
            state.rows[table].find(
              (item) => item.index === state.selected[table].index,
            ) || null;
        renderTable(table);
      }),
    );
    for(const table of ['phase','finding'])renderTable(table);
    refreshTimelineSegments();
    updateSaveState();
  }
  function refreshTimelineSegments() {
    const segments = [];
    for (const table of ["phase", "finding"])
      for (const item of state.rows[table])
        segments.push({
          startSec: parseSec(item.row.start_sec),
          endSec: parseSec(item.row.end_sec),
          label: table === "phase" ? phaseLabel(item.row.phase_label) : "finding",
          kind: table,
          index: item.index,
          item,
        });
    state.timeline.setSegments(segments);
    if (!$("videoPlayer").src)
      state.timeline.setDuration(
        Math.max(0, ...segments.map((segment) => segment.endSec)),
      );
  }
  function setChecks(selector, serialized) {
    const values = new Set(String(serialized || "").split(";"));
    document.querySelectorAll(selector).forEach((input) => {
      input.checked = values.has(input.value);
    });
  }
  function checkedValues(selector) {
    return [...document.querySelectorAll(selector + ":checked")]
      .map((input) => input.value)
      .join(";");
  }
  async function loadRow(table, item, guard = true) {
    if (!item || state.saving || state.pageSaving || state.historyBusy || (guard && !await allowDiscard(table==='case'?['case']:['phase','finding']))) return;
    const previousLoading=state.loading;
    if (table !== "case") setMode("phase",false);
    state.loading = true;
    state.selected[table] = item.placeholder ? null : item;
    const row = item.placeholder ? {start_sec:item.row.start_sec,end_sec:item.row.end_sec} : item.row;
    if (table === "case") {
      $("diagnosisMain").value = [row.diagnosis_main,row.diagnosis_secondary].filter(Boolean).join('；');
      $("caseNotes").value = row.notes || "";
      setChoiceValue("pre_or_post_op", row.pre_or_post_op || "routine");
      if (guard) $("caseDetails").open = true;
    } else {
      $("batchId").value = row.annotation_batch_id || "batch_v1";
      state.batches ||= {};
      state.batches[table] = $("batchId").value;
      state.timeline.setSelection(
        parseSec(row.start_sec),
        parseSec(row.end_sec),
      );
      loadSegmentFields(table,item.placeholder?null:item,row);
      state.ranges.phase=state.ranges.finding=[parseSec(row.start_sec),parseSec(row.end_sec)];
      const other=table==='phase'?'finding':'phase';
      const counterpart=matchingRecord(other,row);
      loadSegmentFields(other,counterpart,row);
      const notes=[state.selected.phase?.row.notes,state.selected.finding?.row.notes].filter(Boolean);
      $('annotationNotes').value=[...new Set(notes)].join('\n');
      seekVideo(parseSec(row.start_sec));
    }
    state.dirty.delete(table);
    delete state.saveErrors[table];
    state.loading = previousLoading;
    for(const kind of ['phase','finding'])renderTable(kind);
    for(const kind of table==='case'?['case']:['phase','finding']) {
      state.formBaseline[kind] = captureForm(kind);
      state.persistedForms[kind] = captureForm(kind);
    }
    updateSaveState();
  }
  function loadSegmentFields(table,item,rangeRow) {
    // Counterpart fields are reset before loading: no findings leak between clips.
    state.ranges[table]=[parseSec(rangeRow.start_sec),parseSec(rangeRow.end_sec)];
    formControls(table).forEach((input,index)=>{
      const field=state.defaultFields[table][index];input.value=field.value;input.checked=field.checked;input.indeterminate=!!field.indeterminate;
    });
    const row=item?.row||{};
    if(table==='phase') {
      setChoiceValue('phase_label',phaseLabel(row.phase_label)||'unused');
      setChoiceValue('phase_quality',phaseQuality(row.quality_level));
      setChoiceValue('visible_side',row.visible_side==='bilateral'?'unknown':row.visible_side||'unknown');
      updatePhaseConditionalUI();
    } else {
      $('clipId').value=row.clip_id || '';state.clipIdAutoValue=$('clipId').value;
      for(const [name,control] of Object.entries(state.findingControls)) {
        let value=row[name];
        const unrecorded=!item && isLegacyLocked();
        if(control.type==='checkbox'){
          control.element.indeterminate=unrecorded || !!item && !['0','1','2'].includes(String(value));
          control.element.checked=item ? ['1','2'].includes(String(value)) : !unrecorded && name==='mucosa_normal';
        }else{
          if(unrecorded){state.choices[name].inputs.forEach(input=>input.checked=false);state.choices[name].value='unknown';continue;}
          if(!item)continue;
          if(name==='turbinate_enlargement' && ['0','1','2'].includes(String(value)))value=value==='2'?'2':'0';
          else if(['turbinate_pale_edema','nasal_meatus_narrowing','airway_obstruction_level','septal_erosion','polyp_like_lesion','bleeding'].includes(name) && ['0','1','2'].includes(String(value)))value=value==='0'?'0':'1';
          if(name==='septal_deviation' && value==='3')value='unknown';
          if(name==='secretion_type' && (row.secretion_present==='0' || ['',undefined].includes(value)))value='none';
          const choice=state.choices[name];
          if(choice.inputs.some(input=>input.value===String(value)))setChoiceValue(name,String(value));
          else {choice.inputs.forEach(input=>input.checked=false);choice.value=String(value || 'unknown');$('finding-'+name).title='原记录未明确此项';}
        }
      }
      updateFindingConditionalUI();
    }
    state.selected[table]=item||null;
    state.batches ||= {};state.batches[table]=row.annotation_batch_id||'batch_v1';
    state.dirty.delete(table);delete state.saveErrors[table];delete state.draftRows[table];
    state.formBaseline[table]=captureForm(table);state.persistedForms[table]=captureForm(table);
  }
  function resetSelection() {
    state.selected[state.mode] = null;
    renderTable(state.mode);
    updateSaveState();
  }
  async function newSegment() {
    if (!clinicalEditable() || state.saving || state.pageSaving || state.historyBusy || !await allowDiscard(['phase','finding'])) return;
    setMode('phase',false);
    state.historySuspended = true;
    resetSelection();
    const other=state.mode==='phase'?'finding':'phase';
    loadSegmentFields(other,null,{start_sec:0,end_sec:0});
    if (state.mode === "phase") {
      $("annotationNotes").value = "";
      setChoiceValue("visible_side", "unknown");
    } else {
      $("annotationNotes").value = "";
      $("clipId").value = "";
      state.clipIdAutoValue = "";
      for (const [name, control] of Object.entries(state.findingControls)) {
        if (control.type === "checkbox") control.element.checked = false;
        else setChoiceValue(name, name === "postoperative_change" ? "2" : "unknown");
      }
    }
    const video = $("videoPlayer"),
      start = Number(video.currentTime) || 0;
    state.timeline.setSelection(
      start,
      Math.min(start + 1, Number(video.duration) || start + 1),
    );
    state.ranges[other]=selectedRange();
    state.formBaseline[other]=captureForm(other);state.persistedForms[other]=captureForm(other);
    for(const table of ['phase','finding'])renderTable(table);
    state.historySuspended = false;
    recordDraft(state.mode);
  }
  function validContext(table) {
    if (!clinicalEditable()) {
      showToast(isLegacyLocked() ? "历史临床标注已锁定，仅可查看。" : "请从视频列表打开已上传的视频后标注。", "error");
      return false;
    }
    if (!$("caseId").value.trim()) {
      showToast("请先从视频列表进入病例。", "error");
      return false;
    }
    if(table==='finding' && !selectedEffectivePhase()){
      state.saveErrors.finding='请先选择已保存的有效观察片段。';
      showToast(state.saveErrors.finding,'error');updateSaveState();return false;
    }
    if (table !== "case") {
      const [start, end] = selectedRange(),
        duration = annotationDuration();
      const rawStart = Number($("startSec").value), rawEnd = Number($("endSec").value);
      if (!$("startSec").value || !$("endSec").value || !Number.isFinite(rawStart) || !Number.isFinite(rawEnd) || rawStart < 0 || rawEnd <= rawStart || end <= start || (duration && end > duration + 0.0005 + 1e-9)) {
        showToast("请选择有效的起止时间，范围应在视频时长之内。", "error");
        return false;
      }
      if (!$("videoId").value.trim()) {
        showToast("请填写视频编号。", "error");
        return false;
      }
    }
    return true;
  }
  function buildRow(table) {
    const existing = state.selected[table]?.row || {},
      [start, end] = selectedRange();
    const row = {
      ...existing,
      case_id: $("caseId").value.trim(),
      visit_id: existing.visit_id || "",
      annotator_id: state.user.username,
      annotation_date: today(),
    };
    if (table === "case")
      return {
        ...row,
        diagnosis_main: $("diagnosisMain").value.trim(),
        diagnosis_secondary: "",
        pre_or_post_op: choiceValue("pre_or_post_op"),
        notes: $("caseNotes").value.trim(),
      };
    Object.assign(row, {
      annotation_batch_id: existing.annotation_batch_id || "",
      video_id: $("videoId").value.trim(),
      start_sec: annotationSec(start),
      end_sec: annotationSec(end),
    });
    if (table === "phase") {
      const effective = choiceValue("phase_label") === "effective_observation";
      return {
        ...row,
        segment_id: existing.segment_id || "seg_" + randomHex8(),
        annotation_schema_version: '2',
        phase_label: choiceValue("phase_label"),
        quality_level: effective ? choiceValue("phase_quality") : "",
        bad_reason: existing.bad_reason || '',
        visible_side: effective ? choiceValue('visible_side') : 'unknown',
        visible_regions: existing.visible_regions || '',
        notes: $("annotationNotes").value.trim(),
      };
    }
    Object.assign(row, {
      clip_id: existing.clip_id || 'finding_'+selectedEffectivePhase().row.segment_id,
      clip_quality: phaseQuality(selectedEffectivePhase().row.quality_level),
      start_sec:selectedEffectivePhase().row.start_sec,end_sec:selectedEffectivePhase().row.end_sec,
      phase_segment_id:selectedEffectivePhase().row.segment_id,annotation_schema_version:'2',
      notes: $("annotationNotes").value.trim(),
    });
    for (const [name, control] of Object.entries(state.findingControls))
      row[name] =
        control.type === "checkbox"
          ? control.element.indeterminate ? (existing[name] || "unknown") : control.element.checked
            ? "1"
            : "0"
          : choiceValue(name);
    row.secretion_present=row.secretion_type==='none'?'0':'1';
    return row;
  }
  async function saveChanges(table,{automatic=false}={}) {
    if (state.saving || state.historyBusy || state.sourceBusy || !validContext(table)) return;
    const beforeRows = clone(state.draftRows[table] || state.rows[table]);
    const beforeForm = clone(state.persistedForms[table] || captureForm(table));
    state.saving = true;
    updateSaveState();
    try {
      const selected = state.selected[table],
        row = buildRow(table);
      let payload;
      try{
        payload=await apiJson(selected?`/clinical/api/rows/${table}/${selected.index}`:'/clinical/api/rows',{
          method:selected?'PUT':'POST',body:JSON.stringify({...(selected?{row}:{table,row}),expected_rows:beforeRows,
            ...(table==='finding'?{phase_ref:{row_id:selectedEffectivePhase().index,expected_row:selectedEffectivePhase().row}}:{})}),
        });
      }catch(error){
        // A lost acknowledgement must not create a second interval. Confirm the
        // exact row we attempted, without writing over a concurrent revision.
        const query=new URLSearchParams({table,case_id:row.case_id,...(table==='case'?{}:{video_id:row.video_id})});
        const current=await apiJson('/clinical/api/rows?'+query).catch(()=>null);
        const confirmed=current?.rows?.find(item=>Object.entries(row).every(([key,value])=>String(item.row[key]??'')===String(value??'')));
        if(!confirmed)throw error;
        payload={updated:confirmed};
      }
      state.selected[table] = payload.updated || payload.created;
      state.dirty.delete(table);
      delete state.draftRows[table];
      delete state.saveErrors[table];
      await refreshAllTables();
      if (!state.selected.case && state.rows.case[0]) state.selected.case = state.rows.case[0];
      await refreshVideoContext().catch(() => notice("标注已保存，视频状态刷新失败，请稍后重新加载。", true));
      switchTable(table);
      rememberSavedChange(table, beforeRows, beforeForm, state.selected[table] && beforeRows.some((item) => item.index === beforeForm.selected?.index) ? "编辑" : "新增");
      updateSaveState(true);
      if(!automatic)showToast(
        table === "case"
          ? "病例信息已保存"
          : table === "phase"
            ? "视频阶段已保存"
            : "征象标注已保存",
      );
      return true;
    } catch (error) {
      state.saveErrors[table] = error.status === 409 ? "保存未完成：已有其他更新，请保留草稿并刷新核对。" : "保存失败：" + error.message;
      showToast("保存失败：" + error.message, "error");
      return false;
    } finally {
      state.saving = false;
      updateSaveState(!state.dirty.has(table));
    }
  }
  function confirmDialog(message) {
    if($("confirmDialog").open)return Promise.resolve(false);
    $("confirmMessage").textContent = message;
    $("confirmDialog").returnValue = "cancel";
    $("confirmDialog").showModal();
    $("confirmCancel").focus();
    return new Promise((resolve) =>
      $("confirmDialog").addEventListener(
        "close",
        () => resolve($("confirmDialog").returnValue === "confirm"),
        { once: true },
      ),
    );
  }
  async function deleteSegment(segment) {
    if(!segment || !clinicalEditable() || state.saving || state.pageSaving || state.historyBusy || state.sourceBusy)return;
    const beforeRows=segmentSnapshot(),deleted={};
    for(const table of ['phase','finding'])deleted[table]=new Set(segment.records[table].map(item=>item.index));
    const current=sameRange(segment.row,{start_sec:selectedRange()[0],end_sec:selectedRange()[1]}) || ['phase','finding'].some(table=>deleted[table].has(state.selected[table]?.index));
    if(!await confirmDialog(`删除 ${fmtSec(segment.row.start_sec)}–${fmtSec(segment.row.end_sec)} 秒片段的全部阶段和征象标注？${current&&(state.dirty.has('phase')||state.dirty.has('finding'))?'该片段未保存的修改也将丢弃。':''}可一起撤销恢复。`))return;
    if(state.saving || state.pageSaving || state.historyBusy)return;
    const before={},afterRows={};
    for(const table of ['phase','finding']) {
      before[table]=clone(state.persistedForms[table] || captureForm(table));
      afterRows[table]=beforeRows[table].filter(item=>!deleted[table].has(item.index));
    }
    state.saving=true;updateSaveState();
    try {
      const payload=await apiJson('/clinical/api/annotation-history',{method:'POST',body:JSON.stringify({
        case_id:$('caseId').value,video_id:$('videoId').value,table:'segment',expected_rows:beforeRows,restore_rows:afterRows})});
      Object.assign(state.rows,payload.rows);
      let cleared=false;const after={};
      for(const table of ['phase','finding']) {
        if(current) {
          loadSegmentFields(table,null,segment.row);cleared=true;
          after[table]=captureForm(table);state.persistedForms[table]=clone(after[table]);
        } else {
          if(state.draftRows[table])state.draftRows[table]=state.draftRows[table].filter(item=>!deleted[table].has(item.index));
          after[table]=clone(state.persistedForms[table] || captureForm(table));
        }
      }
      if(cleared)$('annotationNotes').value=[...new Set([state.selected.phase?.row.notes,state.selected.finding?.row.notes].filter(Boolean))].join('\n');
      for(const table of ['phase','finding'])if(current) {
        after[table]=captureForm(table);state.formBaseline[table]=clone(after[table]);state.persistedForms[table]=clone(after[table]);
      }
      state.undoStack=state.undoStack.filter(item=>!(item.kind==='draft' && (current || deleted[item.table]?.has(item.after.selected?.index))));
      state.undoStack.push({kind:'saved',table:'segment',action:'删除',beforeRows,afterRows:segmentSnapshot(),before,after});state.redoStack=[];
      renderTable('phase');refreshTimelineSegments();
      await refreshVideoContext().catch(()=>notice('片段已删除，视频状态刷新失败。',true));
      showToast('本片段的阶段和征象已一起删除，可撤销恢复');
    } catch(error) {showToast('删除片段失败：'+error.message,'error');}
    finally {state.saving=false;updateSaveState();}
  }
  async function deleteRow(table, explicit=null) {
    if (!clinicalEditable() || state.saving || state.pageSaving || state.historyBusy || state.sourceBusy) return;
    const selected = explicit ? state.rows[table].find(item=>item.index===explicit.index) : state.selected[table];
    const preserveDraft=!!explicit&&state.dirty.has(table)&&state.selected[table]?.index!==selected?.index;
    if (
      !selected ||
      !(await confirmDialog(
        table === "case"
          ? "删除当前病例信息？阶段和征象记录会保留。"
          : preserveDraft ? "删除这条已保存标注？当前未保存的草稿会保留，可撤销删除。" : "删除这条已保存标注？可通过撤销恢复已保存版本。",
      ))
    )
      return;
    if (state.saving || state.historyBusy) return;
    const beforeRows = clone(state.rows[table]);
    const beforeForm = clone(state.persistedForms[table] || captureForm(table));
    state.saving = true;
    updateSaveState();
    try {
      await apiJson(`/clinical/api/rows/${table}/${selected.index}`, {
        method: "DELETE", body: JSON.stringify({expected_rows: beforeRows}),
      });
      const deletedCurrent=state.selected[table]?.index===selected.index;
      if(deletedCurrent)state.selected[table] = null;
      if(!preserveDraft)state.dirty.delete(table);
      if(preserveDraft && state.draftRows[table])state.draftRows[table]=state.draftRows[table].filter(item=>item.index!==selected.index);
      else delete state.draftRows[table];
      delete state.saveErrors[table];
      await refreshAllTables();
      await refreshVideoContext().catch(() => notice("标注已删除，视频状态刷新失败，请稍后重新加载。", true));
      if(table==='finding' && deletedCurrent && !preserveDraft){
        loadSegmentFields('finding',null,selectedEffectivePhase()?.row || {start_sec:selectedRange()[0],end_sec:selectedRange()[1]});
        if(!state.dirty.has('phase'))$('annotationNotes').value=state.selected.phase?.row.notes || '';
      }
      rememberSavedChange(table, beforeRows, beforeForm, "删除",preserveDraft);
      showToast("记录已删除，可使用撤销恢复");
    } catch (error) {
      state.saveErrors[table] = "删除失败：" + error.message;
      showToast(error.message, "error");
    } finally {state.saving = false;updateSaveState();}
  }
  function updatePositionLabel() {
    const video = $("videoPlayer");
    const text=`${(video.currentTime || 0).toFixed(1)} / ${(video.duration || 0).toFixed(1)} 秒`;
    if ($("positionLabel").textContent !== text) $("positionLabel").textContent=text;
  }
  function stopPlayheadUpdates() {
    if(state.playheadFrame !== null) cancelAnimationFrame(state.playheadFrame);
    state.playheadFrame=null;
  }
  function startPlayheadUpdates() {
    stopPlayheadUpdates();
    const tick=()=>{
      state.playheadFrame=null;
      const video=$('videoPlayer');
      if(video.paused || video.ended || state.stagingVideo)return;
      state.timeline.setCurrent(video.currentTime || 0);
      updatePositionLabel();
      state.playheadFrame=requestAnimationFrame(tick);
    };
    state.playheadFrame=requestAnimationFrame(tick);
  }
  function updatePlayButton() {
    $("playToggle").innerHTML = $("videoPlayer").paused
      ? '<svg viewBox="0 0 24 24"><path d="m8 5 11 7-11 7Z"/></svg>'
      : '<svg viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></svg>';
  }
  async function togglePlayback() {
    const video = $("videoPlayer");
    if (!video.src) return;
    state.previewing = false;
    try {
      if (video.paused) await video.play();
      else video.pause();
    } catch {
      showToast("视频暂时无法播放，请检查原视频或服务连接。", "error");
    }
  }
  function seekVideo(sec) {
    const video = $("videoPlayer");
    if (!video.src || !Number.isFinite(sec)) return;
    state.previewing = false;
    video.currentTime = Math.max(
      0,
      Math.min(sec, Number(video.duration) || sec),
    );
    state.timeline?.setCurrent(video.currentTime || 0);
    updatePositionLabel();
  }
  function seekRelative(delta) {
    seekVideo(($("videoPlayer").currentTime || 0) + delta);
  }
  async function previewSelection() {
    const video = $("videoPlayer"),
      [start, end] = selectedRange();
    if (!video.src || end <= start) return;
    video.currentTime = start;
    state.previewing = true;
    try {
      await video.play();
    } catch {
      showToast("无法播放当前片段。", "error");
      state.previewing = false;
    }
  }
  function isLegacyLocked() {
    return !!(state.workflowVideo?.locked_legacy || state.videoContext?.locked_legacy || state.caseProfile?.locked);
  }
  function clinicalEditable() {
    return !!(state.workflowVideo && state.sourceKind==='remote') && !isLegacyLocked() && !state.loading && !state.switchingVideo;
  }
  function updateVideoControls() {
    const busy = state.sourceBusy || state.saving || state.pageSaving || state.historyBusy;
    $("currentVideoText").textContent = $("videoId").value || "未选择";
    $("currentVideoText").title = $("videoId").value;
    $("caseId").title = $("caseId").value;
    $("loadRemoteVideo").disabled = busy || !state.workflowVideo;
    const filename = state.workflowVideo?.original_filename || state.videoContext?.asset?.filename || "";
    $("videoName").textContent = filename || "尚未关联上传视频";
    $("videoName").title = filename;
    $("uploadState").textContent = state.workflowVideo ? "视频已上传" : "视频未上传";
    $("uploadState").classList.toggle("good", !!state.workflowVideo);
    $("casePersistence").textContent = state.videoContext?.case_exists ? "病例已保存" : "";
    $("playbackSource").textContent = state.sourceBusy ? "加载中…" : state.sourceKind === "remote" ? "本机视频库" : "未加载视频";
    $("reviewStatus").hidden = !state.workflowVideo;
    $("reviewStatus").textContent = isLegacyLocked() ? "历史已归档" : state.workflowVideo?.review_state === "reviewed" ? "已完成复核" : "待复核";
    $("reviewStatus").classList.toggle("good", !!state.workflowVideo && (isLegacyLocked() || state.workflowVideo.review_state === "reviewed"));
    $("reviewStatus").title = "阶段修改或撤销已保存的阶段后，需要重新完成复核。";
    $("reviewButton").hidden = !state.workflowVideo || isLegacyLocked();
    $("legacyLockNotice").hidden = !isLegacyLocked();
    const navigation=state.videoNavigation;
    $('previousVideo').disabled=busy||state.navigationPending||!navigation.previous;
    $('nextVideo').disabled=busy||state.navigationPending||!navigation.next;
    $('videoPosition').textContent=navigation.total?`${navigation.position}/${navigation.total}`:'';
    $('videoPosition').title=navigation.total?`第 ${navigation.position} 个视频，共 ${navigation.total} 个`:'';
  }
  async function refreshVideoContext() {
    const params = new URLSearchParams({case_id: $("caseId").value, video_id: $("videoId").value});
    state.videoContext = await apiJson("/clinical/api/videos/context?" + params);
    state.workflowVideo = state.videoContext.remote_video;
    state.cropOverlay?.load(state.workflowVideo?.id);
    updateVideoControls();
  }
  function setVideoSource(url) {
    if (!url) throw Error("已上传视频缺少播放地址，请返回视频列表检查关联。");
    const video = $("videoPlayer"), position = video.currentTime || 0;
    video.pause();
    state.sourceKind = "remote";
    state.previewing = false;
    video.src = url;
    video.addEventListener("loadedmetadata", () => {
      video.currentTime = Math.min(position, video.duration || 0);
    }, {once:true});
    video.load();
    $("videoStage").classList.add("has-video");
    updateVideoControls();
  }
  async function loadRemoteVideo(refresh = true) {
    if (state.sourceBusy || state.saving || state.historyBusy) return;
    state.sourceBusy = true;
    updateSaveState();
    try {
      if (refresh) await refreshVideoContext();
      if (!state.workflowVideo) throw Error("此病例尚未关联已上传视频，请返回视频列表完成关联后再标注。");
      setVideoSource(state.workflowVideo.media_url);
      const url=new URL(location.href);url.searchParams.set('source','remote');history.replaceState(null,'',url);
      notice("");
    } catch (error) {
      $("videoEmptyState").querySelector("strong").textContent = "视频未能加载";
      notice(error.message, true);
    } finally {state.sourceBusy = false; updateSaveState();}
  }
  function loadVideoElement(url,position=0){
    const video=$('videoPlayer');
    return new Promise((resolve,reject)=>{
      const finish=error=>{
        clearTimeout(timer);video.removeEventListener('loadedmetadata',loaded);video.removeEventListener('error',failed);
        error?reject(error):resolve();
      };
      const loaded=()=>{video.currentTime=Math.max(0,Math.min(position,video.duration||0));finish();};
      const failed=()=>finish(Error('视频加载失败，请检查连接后重试；原视频标注已保留'));
      const timer=setTimeout(()=>finish(Error('视频加载超时，请稍后重试；原视频标注已保留')),20000);
      video.addEventListener('loadedmetadata',loaded);video.addEventListener('error',failed);
      video.pause();video.src=url;video.load();
    });
  }
  function applyVideoContext(payload){
    const identity=payload.video;
    const mode=state.mode;state.loading=true;state.historySuspended=true;
    clearTimeout(state.toastTimer);$('toast').classList.remove('show');$('annotationNotes').value='';
    for(const table of ['case','phase','finding'])formControls(table).forEach((input,index)=>{
      const field=state.defaultFields[table][index];input.value=field.value;input.checked=field.checked;input.indeterminate=!!field.indeterminate;
    });
    for(const choice of Object.values(state.choices))choice.value=choice.select?choice.select.value:choice.inputs.find(input=>input.checked)?.value||'';
    state.rows=payload.rows;state.selected={case:null,phase:null,finding:null};state.ranges={};state.batches={};
    state.dirty.clear();state.draftRows={};state.saveErrors={};state.profileError='';state.undoStack=[];state.redoStack=[];
    state.formBaseline={};state.persistedForms={};state.clipIdAutoValue='';state.previewing=false;
    state.workflowVideo=payload.video;$("exportButton").href=`/api/videos/${payload.video.id}/export.zip`;state.videoContext=payload.context;state.videoNavigation=payload.navigation;
    $('caseId').value=identity.case_id;$('videoId').value=identity.clinical_video_id;
    $('annotatorId').value=state.user.username;
    showCaseProfile(payload.profile);
    state.timeline.dragging=false;state.timeline.dragMode=null;
    updateTimeLimits();state.timeline.setCurrent(0);
    state.timeline.viewStartSec=0;state.timeline.viewDurationSec=Math.max(3,identity.duration_seconds||3);
    $('startSec').value='0';$('endSec').value=fmtSec(Math.min(1,identity.duration_seconds||1));
    state.timeline.setSelection(...selectedRange(),false);
    for(const table of ['case','phase','finding'])renderTable(table);
    refreshTimelineSegments();
    if(state.rows.case[0])loadRow('case',state.rows.case[0],false);
    setMode(mode,false);
    const firstSegment=state.rows.phase.find(item=>phaseLabel(item.row.phase_label)==='effective_observation') || segmentRows('phase')[0];
    if(firstSegment)loadRow('phase',firstSegment,false);
    state.loading=false;state.historySuspended=false;state.dirty.clear();
    for(const table of ['case','phase','finding']){state.formBaseline[table]=captureForm(table);state.persistedForms[table]=captureForm(table);}
    updatePositionLabel();state.timeline.setCurrent($('videoPlayer').currentTime||0);
    state.cropOverlay?.load(payload.video?.id);updateSaveState();
  }
  async function navigateVideo(direction){
    const target=state.videoNavigation[direction<0?'previous':'next'];
    if(!target||state.navigationPending||state.sourceBusy||state.historyBusy||state.profileSaving)return;
    state.navigationPending=true;updateVideoControls();
    const video=$('videoPlayer'),previous={url:video.getAttribute('src'),time:video.currentTime||0,id:state.workflowVideo?.id,rate:video.playbackRate};
    let mediaChanged=false;
    try{
      if(state.phaseSavePromise)await state.phaseSavePromise;
      if(state.findingSavePromise)await state.findingSavePromise;
      if(!await allowDiscard(['case','phase','finding','profile'],{preserve:true}))return;
      state.switchingVideo=true;state.sourceBusy=true;$('workspace').inert=true;updateSaveState();
      const payload=await apiJson(`/clinical/api/videos/${encodeURIComponent(target.id)}/editor-context`);
      if(payload.video.id!==target.id)throw Error('视频响应不一致，请重新加载列表');
      state.stagingVideo=true;state.cropOverlay?.load(null);mediaChanged=true;
      const start=Number(payload.rows[state.mode]?.[0]?.row.start_sec)||0;
      await loadVideoElement(payload.video.media_url,start);
      video.playbackRate=previous.rate;
      state.sourceKind='remote';
      applyVideoContext(payload);
      const url=new URL(location.href);url.searchParams.delete('case_id');url.searchParams.delete('video_id');url.searchParams.set('workflow_video_id',payload.video.id);url.searchParams.set('mode',state.mode);
      url.searchParams.set('source','remote');history.replaceState(null,'',url);notice('');
    }catch(error){
      if(mediaChanged&&previous.url){
        try{await loadVideoElement(previous.url,previous.time);video.playbackRate=previous.rate;}catch(_){}
        state.cropOverlay?.load(previous.id);
      }
      notice(error.message,true);showToast(error.message,'error');
    }finally{
      state.stagingVideo=false;state.switchingVideo=false;state.sourceBusy=false;state.navigationPending=false;
      $('workspace').inert=false;updateSaveState();
    }
  }
  async function loadCaseProfile() {
    if (!$("caseId").value) return;
    const profile = await apiJson("/clinical/api/case-profile?" + new URLSearchParams({case_id: $("caseId").value}));
    showCaseProfile(profile);
  }
  function profileDetails() {
    return {sex:choiceValue('case_sex'),age:$('patientAge').value.trim()};
  }
  function setProfileFields(profile) {
    setChoiceValue('case_sex',profile?.details?.sex || '');
    $('patientAge').value=profile?.details?.age || '';
  }
  function showCaseProfile(profile){
    state.caseProfile=profile;
    setProfileFields(profile);
    updateCaseProfileState();
  }
  function caseProfileDirty() {
    return Object.entries(profileDetails()).some(([key,value])=>value!==(state.caseProfile?.details?.[key] || ''));
  }
  function updateCaseProfileState() {
    document.querySelectorAll('[data-profile]').forEach(input=>input.disabled=!state.caseProfile||state.profileSaving||state.pageSaving||state.switchingVideo||isLegacyLocked());
    const message=state.profileSaving ? '正在保存病例信息…' : state.profileError || (caseProfileDirty() ? '病例信息未保存 · Ctrl S 保存' : '');
    $('caseProfileState').textContent=message;
    $('caseProfileState').title=message;
    $('caseProfileState').hidden=!message;
    $('caseSaveState').hidden=!!message;
    $('caseProfileState').classList.toggle('error',!!state.profileError);
  }
  async function saveCaseProfile() {
    if(!state.caseProfile || state.profileSaving)return false;
    if(!caseProfileDirty())return true;
    state.profileSaving=true;state.profileError='';updateCaseProfileState();
    try {
      const profile=await apiJson('/clinical/api/case-profile?'+new URLSearchParams({case_id:$('caseId').value}),{
        method:'PUT',body:JSON.stringify({
          // The name and removed history fields are retained. Only visible
          // demographic fields are patched into the existing profile.
          patient_name:state.caseProfile.patient_name || '',details:profileDetails(),
          expected_patient_name:state.caseProfile.patient_name || '',expected_revision:state.caseProfile.revision,
        }),
      });
      showCaseProfile(profile);return true;
    }catch(error){state.profileError='病例信息保存失败：'+error.message;showToast(state.profileError,'error');return false;}
    finally{state.profileSaving=false;updateCaseProfileState();updateSaveState();}
  }
  async function saveCaseOverview(){
    if(!await saveCaseProfile())return false;
    if(isLegacyLocked())return true;
    return saveChanges('case');
  }
  async function reviewVideo() {
    if(!state.workflowVideo)return;
    if (!clinicalEditable() || state.saving || state.pageSaving || state.sourceBusy || state.historyBusy || state.profileSaving) return;
    if (state.dirty.size || caseProfileDirty()) {
      showToast("请先保存病例信息和各标注页签的修改，再完成复核。", "error");
      return;
    }
    if (
      !(await confirmDialog(
        "确认当前阶段与征象已保存并核对无误？复核结果将保存在本机。",
      ))
    )
      return;
    if (state.saving || state.historyBusy) return;
    state.saving = true;
    updateSaveState();
    try {
      const result = await apiJson(
        `/annotation/api/workflow/videos/${state.workflowVideo.id}/review`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_revision: state.workflowVideo.revision,
            expected_rows: clone(state.rows.phase),
          }),
        },
      );
      state.workflowVideo = result.video;
      await refreshAllTables();
      const message = result.unchanged ? "复核结果未变化" : result.job_id
        ? `复核已保存；${result.effective_segments ? result.effective_segments + "个有效片段" : "有效观察片段"}的抽帧任务已创建，可在任务面板查看进度`
        : "复核已保存到本机";
      notice(message);
      showToast(message);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      state.saving = false;
      updateSaveState();
    }
  }
  function bindEvents() {
    const video = $("videoPlayer");
    document.querySelectorAll('[data-diagnosis]').forEach(button=>button.addEventListener('click',()=>{
      const input=$('diagnosisMain');if(input.disabled || input.readOnly)return;
      if(button.dataset.diagnosis)input.value=button.dataset.diagnosis;
      input.focus();input.dispatchEvent(new Event('input',{bubbles:true}));
    }));
    if (window.VideoCropOverlay) state.cropOverlay = new window.VideoCropOverlay(video, $("videoStage"), $("videoCropOverlay"), $("toggleCropOverlay"), $("cropOverlayStatus"));
    $("loadRemoteVideo").addEventListener("click", () => loadRemoteVideo());
    $('previousVideo').addEventListener('click',()=>navigateVideo(-1));
    $('nextVideo').addEventListener('click',()=>navigateVideo(1));
    document.querySelectorAll("[data-profile]").forEach(input=>input.addEventListener("input",updateCaseProfileState));
    $("undoAnnotation").addEventListener("click", () => travelHistory("undo"));
    $("redoAnnotation").addEventListener("click", () => travelHistory("redo"));
    $("videoStage").addEventListener("click", (event) => {
      if (!event.target.closest("button") && video.src) togglePlayback();
    });
    $("videoStage").addEventListener("keydown", (event) => {
      if (event.target === event.currentTarget && event.key === "Enter") {
        event.preventDefault(); togglePlayback();
      }
    });
    // File drops must not replace this page or open a local annotation source.
    for (const name of ["dragover", "drop"])
      document.addEventListener(name, (event) => {if (event.dataTransfer?.types.includes("Files")) event.preventDefault();});
    $("playToggle").addEventListener("click", togglePlayback);
    $("backButton").addEventListener("click", () => seekRelative(-3));
    $("forwardButton").addEventListener("click", () => seekRelative(3));
    $("playbackRate").addEventListener("change", () => {
      video.playbackRate = Number($("playbackRate").value);
    });
    $("previewSelection").addEventListener("click", previewSelection);
    $("fitTimeline").addEventListener("click", () => {
      state.timeline.viewStartSec = 0;
      state.timeline.viewDurationSec = Math.max(3, state.timeline.durationSec);
      state.timeline.draw();
    });
    video.addEventListener("loadedmetadata", () => {
      if(state.stagingVideo)return;
      updateTimeLimits();
      const [start, end] = selectedRange();
      const previousLoading = state.loading;
      state.loading = true;
      if (end <= start)
        state.timeline.setSelection(0, Math.min(1, video.duration || 1));
      else state.timeline.setSelection(start, end);
      state.loading = previousLoading;
      updatePositionLabel();
      if (!state.dirty.has(state.mode)) {state.formBaseline[state.mode] = captureForm(state.mode);state.persistedForms[state.mode] = captureForm(state.mode);}
    });
    video.addEventListener("error", () => {
      if(state.stagingVideo)return;
      notice(
        "视频暂时无法播放，请检查服务连接后点“重新加载”。已保存标注与当前草稿会保留。",
        true,
      );
    });
    video.addEventListener("timeupdate", () => {
      if(state.stagingVideo)return;
      if (state.previewing && video.currentTime >= selectedRange()[1]) {
        if ($("loopSelection").checked) video.currentTime = selectedRange()[0];
        else {
          video.pause();
          state.previewing = false;
        }
      }
      state.timeline.setCurrent(video.currentTime || 0);
      updatePositionLabel();
    });
    video.addEventListener("play", () => {updatePlayButton();startPlayheadUpdates();});
    video.addEventListener("pause", () => {updatePlayButton();stopPlayheadUpdates();});
    for (const id of ["startSec", "endSec"])
      $(id).addEventListener("input", () => {
        state.timeline.setSelection(...selectedRange(), false);
        state.ranges[state.mode] = selectedRange();
        markDirty(state.mode);
        $("rangeSummary").textContent =
          selectedRange().map(fmtSec).join(" – ") + " 秒";
      });
    $("newSegment").addEventListener("click", newSegment);
    for (const button of document.querySelectorAll("[data-mode-tab]"))
      button.addEventListener("click", async event => {
        event.preventDefault();
        const mode=button.dataset.modeTab,range=selectedRange(),row={start_sec:range[0],end_sec:range[1]};
        // Untouched counterpart follows the current exact range. An edited draft
        // retains its own range until saved or explicitly discarded.
        if(mode!==state.mode&&!state.dirty.has(mode)) {
          loadSegmentFields(mode,matchingRecord(mode,row),row);
          state.ranges[mode]=range;
        }
        setMode(mode);
        for(const table of ['phase','finding'])renderTable(table);
      });
    for(const tabs of document.querySelectorAll('.record-tabs,.mode-tabs'))tabs.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key))event.stopPropagation();});
    for (const table of ["phase", "finding"]) {
      const load = (event) => {
        const button=event.target.closest('[data-delete-segment]');
        if(button){event.preventDefault();event.stopPropagation();if(!button.disabled)deleteSegment(segmentRows('phase').find(item=>item.index===Number(button.dataset.rowIndex) && (item.sourceTable||'phase')===button.dataset.deleteSegment));return;}
        const row = event.target.closest("tr[data-index]");
        if (row)
          loadRow(
            table,
            segmentRows(table).find(
              (item) => item.index === Number(row.dataset.index) && (item.sourceTable||table)===row.dataset.sourceTable,
            ),
          );
      };
      tableElement(table).addEventListener("click", load);
      tableElement(table).addEventListener("keydown", (event) => {
        if (event.key === "Enter"&&!event.target.closest('[data-delete-segment]')) {
          event.preventDefault();
          load(event);
        }
      });
    }
    $('retryPhaseSave').addEventListener('click',savePageAnnotations);
    $('savePageAnnotations').addEventListener('click',savePageAnnotations);
    $('annotationNotes').addEventListener('input',()=>{
      if(!state.selected.phase && state.selected.finding)markDirty('finding');
      else markDirty('phase');
      if(choiceValue('phase_label')==='effective_observation' && (state.selected.finding || state.dirty.has('finding')))markDirty('finding');
    });
    $("saveCase").addEventListener("click", saveCaseOverview);
    $("reviewButton").addEventListener("click", reviewVideo);
    for (const [id, section] of [
      ["phaseEditor", "phase"],
      ["findingEditor", "finding"],
      ["caseDetails", "case"],
    ]) {
      for (const name of ["input", "change", "compositionend"])
        $(id).addEventListener(name, (event) => {
          if (!event.isComposing && event.target.matches("input,select,textarea")&&!event.target.readOnly&&!event.target.dataset.profile) markDirty(section);
        });
    }
    for(const table of ['phase','finding'])$(table+'Editor').addEventListener('focusin',()=>{state.mode=table;document.body.dataset.mode=table;});
    $('findingEditor').addEventListener('click',event=>{if(event.target.matches('input[type=radio]')&&!event.target.disabled)markDirty('finding');});
    document.addEventListener("keydown", (event) => {
      if(state.switchingVideo){event.preventDefault();return;}
      if ($("confirmDialog").open || event.isComposing) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && !event.target.closest(".patient-profile,#caseDetails")) {
        event.preventDefault();
        if (!event.repeat) travelHistory(event.shiftKey ? "redo" : "undo");
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (!event.repeat){
          savePageAnnotations();
        }
        return;
      }
      if (
        event.target.closest("input,select,textarea,button,a,[contenteditable]")
      )
        return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.code === "Space") {
        event.preventDefault();
        togglePlayback();
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        seekRelative(-3);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        seekRelative(3);
      }
    });
    document.addEventListener('click',async event=>{
      if(state.navigationApproved)return;
      const anchor=event.target.closest('a[href]'),logout=event.target.closest('[data-shell-logout]');
      if(!logout&&(!anchor||anchor.hasAttribute('download')||anchor.id==='exportButton'||anchor.target==='_blank'||event.ctrlKey||event.metaKey))return;
      if(!state.dirty.size&&!caseProfileDirty()&&!state.saving&&!state.pageSaving&&!state.profileSaving&&!state.switchingVideo)return;
      event.preventDefault();event.stopImmediatePropagation();
      if(state.saving||state.pageSaving||state.historyBusy||state.profileSaving||state.switchingVideo){showToast('正在保存或切换视频，请等待完成后再离开。','error');return;}
      if(await allowDiscard(['case','phase','finding','profile'])){
        state.navigationApproved=true;(logout||anchor).click();
      }
    },true);
    window.addEventListener("beforeunload", (event) => {
      if(state.navigationApproved)return;
      if (state.dirty.size || caseProfileDirty() || state.sourceBusy || state.saving || state.pageSaving || state.historyBusy || state.profileSaving) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
  }
  async function init() {
    try {
      const params = new URLSearchParams(location.search),
        workflowId = params.get("workflow_video_id");
      const [me,config,initialContext] = await Promise.all([
        apiJson("/annotation/api/me"), apiJson("/clinical/api/schema"),
        workflowId ? apiJson("/clinical/api/videos/" + encodeURIComponent(workflowId) + '/editor-context') : null,
      ]);
      state.user = me.user;
      window.WorkspaceShell.render(state.user, "videos", me.features);
      state.config = config;
      configureCatalog(state.config.catalog);
      $("annotatorId").value = state.user.username;
      $("annotatorText").textContent=state.user.username;
      $("annotatorText").title=state.user.username;
      setupControls();
      for(const table of ['case','phase','finding'])state.defaultFields[table]=formControls(table).map(input=>({value:input.value,checked:input.checked,indeterminate:input.indeterminate}));
      state.timeline = new Timeline($("timelineCanvas"), {
        canEdit: () => clinicalEditable() && !state.saving && !state.pageSaving && !state.historyBusy,
        onEditStart: () => {setMode('phase',false);state.timelineEditing = true;},
        onEditEnd: () => {state.timelineEditing = false;recordDraft(state.mode);},
        isSelected: (segment) =>
          state.selected[segment.kind]?.index === segment.index,
        onSelectionChange: syncSelectionInputs,
        onSeek: seekVideo,
        canSeek: () => !!$('videoPlayer').src && $('videoPlayer').readyState >= 1 && !state.sourceBusy && !state.switchingVideo,
        onScrubStart: () => {
          const video=$('videoPlayer');state.scrubResume=!video.paused;video.pause();state.previewing=false;
        },
        onScrubEnd: () => {
          const video=$('videoPlayer');
          if(state.scrubResume&&!state.sourceBusy&&!state.switchingVideo&&video.currentTime<annotationDuration()-.0005)video.play().catch(()=>{});
          state.scrubResume=false;
        },
        onSegmentSelect: async segment => {if(state.selectionBusy)return;state.selectionBusy=true;try{await loadRow(segment.kind,segment.item);}finally{state.selectionBusy=false;}},
      });
      bindEvents();
      if (workflowId) {
        const payload = initialContext;
        if (!payload.video.managed) throw Error("请从视频列表选择待复核视频。");
        state.workflowVideo = payload.video;
        $("exportButton").href = `/api/videos/${payload.video.id}/export.zip`;
        state.videoNavigation = payload.navigation;
        $("caseId").value = payload.video.case_id;
        $("videoId").value = payload.video.clinical_video_id;
        $("videoId").readOnly = true;
        $("reviewButton").hidden = false;
      } else if (params.has("case_id")) {
        $("caseId").value = params.get("case_id");
        $("videoId").value = params.get("video_id") || "";
        $("videoId").readOnly = true;
      }
      if ($("caseId").value) {
        // Begin the range-based video read as soon as its authorized URL is
        // available. Case fields and annotations do not gate media metadata.
        await Promise.all([
          refreshVideoContext().then(() => state.workflowVideo && loadRemoteVideo(false)),
          loadCaseProfile(), refreshAllTables(),
        ]);
      } else await refreshAllTables();
      if(state.workflowVideo&&!state.videoNavigation.total){
        const payload=await apiJson(`/clinical/api/videos/${state.workflowVideo.id}/editor-context`);
        state.videoNavigation=payload.navigation;
      }
      if (state.rows.case[0]) loadRow("case", state.rows.case[0], false);
      const mode = params.get("mode") || state.mode;
      setMode(mode, false);
      const first = state.rows.phase.find(item=>phaseLabel(item.row.phase_label)==='effective_observation') || segmentRows('phase')[0];
      if (first) loadRow('phase', first, false);
      state.dirty.clear();
      state.loading = false;
      for (const table of ["case", "phase", "finding"]) {state.formBaseline[table] = captureForm(table);state.persistedForms[table] = captureForm(table);}
      updateSaveState();
      $("workspace").setAttribute("aria-busy", "false");
      if (!state.workflowVideo) {
        $("videoEmptyState").querySelector("strong").textContent = "尚未关联已上传视频";
        $('videoEmptyState').querySelector('span:not(.empty-play)').textContent='请从病例列表上传或关联视频后进入标注';
        $('videoEmptyState').querySelector('small').textContent='已有标注记录保持保留';
        notice('请从病例列表上传或关联视频后进入标注。');
      }
    } catch (error) {
      notice("无法加载标注工作区：" + error.message, true);
      $("workspace").setAttribute("aria-busy", "false");
    }
  }
  init();
})();
