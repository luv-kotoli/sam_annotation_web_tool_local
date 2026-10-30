(() => {
  "use strict";
  // Coordinates stay in original pixels. SVG is positioned over the contained
  // video image, accounting for letterboxing at every responsive size.
  class VideoCropOverlay {
    constructor(video, stage, svg, button, status) {
      Object.assign(this, {video, stage, svg, button, status, record:null, enabled:true, timer:null, generation:0});
      button.addEventListener("click", () => {
        this.enabled = !this.enabled;
        button.setAttribute("aria-pressed", String(this.enabled));
        this.draw();
      });
      for (const event of ["loadedmetadata", "resize", "timeupdate", "seeked", "emptied"])
        video.addEventListener(event, () => this.draw());
      new ResizeObserver(() => this.draw()).observe(stage);
    }
    async load(videoId) {
      const generation = ++this.generation;
      clearTimeout(this.timer);
      this.record = null; this.button.hidden = true; this.svg.setAttribute("hidden", "");
      this.status.hidden = true;
      if (!videoId) return;
      const poll = async () => {
        try {
          const response = await fetch(`/annotation/api/workflow/videos/${encodeURIComponent(videoId)}/crop-record`,
            {credentials:"same-origin", headers:{"X-Requested-With":"XMLHttpRequest"}});
          if (!response.ok) throw Error("裁剪框暂时无法读取");
          const data = await response.json();
          if (generation !== this.generation) return;
          if (data.state === "stored" && data.record?.schema === "sam-workflow.video-crop.v1") {
            this.record = data.record; this.button.hidden = false; this.status.hidden = true; this.draw();
            this.timer = setTimeout(poll, 10000);
          } else if (data.state === "pending") {
            this.status.textContent = "AI 裁剪框待保存"; this.status.hidden = false;
            this.timer = setTimeout(poll, 5000);
          } else {
            this.status.hidden = true;
            // Upload-triggered inference may start after this editor opened.
            // Continue discovery so its real crop appears without a page reload.
            this.timer = setTimeout(poll, 10000);
          }
        } catch (_) {
          if (generation !== this.generation) return;
          this.status.textContent = "裁剪框暂不可用，视频可继续播放"; this.status.hidden = false;
          this.timer = setTimeout(poll, 15000);
        }
      };
      await poll();
    }
    draw() {
      const {record, video, svg} = this;
      const width = video.videoWidth, height = video.videoHeight;
      if (!this.enabled || !record || !width || !height || record.video.width !== width || record.video.height !== height) {
        svg.setAttribute("hidden", ""); return;
      }
      const time = video.currentTime || 0;
      const interval = record.intervals.find((item, index) => time >= item.start_sec &&
        (time < item.end_sec || index === record.intervals.length - 1 && time <= item.end_sec + .001));
      if (!interval) {svg.setAttribute("hidden", ""); return;}
      const box = video.getBoundingClientRect(), parent = this.stage.getBoundingClientRect();
      const scale = Math.min(box.width / width, box.height / height);
      const w = width * scale, h = height * scale;
      Object.assign(svg.style, {left:`${box.left-parent.left+(box.width-w)/2}px`, top:`${box.top-parent.top+(box.height-h)/2}px`, width:`${w}px`, height:`${h}px`});
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      svg.querySelector("polygon").setAttribute("points", interval.points.map(p => p.join(",")).join(" "));
      svg.removeAttribute("hidden");
    }
  }
  window.VideoCropOverlay = VideoCropOverlay;
})();
