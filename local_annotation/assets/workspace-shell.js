"use strict";
(() => {
  const paths = {
    home: '<path d="m3 11 9-8 9 8M5 10v11h14V10M9 21v-7h6v7"/>',
    video:
      '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m10 8 6 4-6 4Z"/>',
    frames:
      '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="m3 16 6-6 5 5 3-3 4 4"/><circle cx="16" cy="8" r="1"/>',
    dataset:
      '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
    train: '<path d="m9 5 12 7-12 7Z"/><path d="M3 5v14"/>',
    diagnosis: '<path d="M8 3v6a4 4 0 0 0 8 0V3M6 3h4M14 3h4M12 13v3a5 5 0 0 0 10 0v-2"/><circle cx="21" cy="11" r="2"/>',
    tasks: '<path d="M9 5h12M9 12h12M9 19h12M3 4l1 1 2-2M3 11l1 1 2-2M3 18l1 1 2-2"/>',
    settings:
      '<path d="M3 6h18M3 12h18M3 18h18"/><circle cx="8" cy="6" r="2" fill="white"/><circle cx="16" cy="12" r="2" fill="white"/><circle cx="10" cy="18" r="2" fill="white"/>',
    users:
      '<circle cx="9" cy="7" r="3"/><path d="M3 21v-4a6 6 0 0 1 12 0v4M16 4a3 3 0 0 1 0 6M18 13a5 5 0 0 1 3 4v4"/>',
    plus: '<path d="M12 4v16M4 12h16"/>',
    upload: '<path d="M4 16v5h16v-5M12 17V3m-6 6 6-6 6 6"/>',
    refresh:
      '<path d="M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 14 6M18 18a8 8 0 0 1-14-6"/>',
    edit: '<path d="m4 16 12-12 4 4L8 20H4ZM13 7l4 4"/>',
    check: '<path d="m4 12 5 5L20 6"/>',
    back: '<path d="m12 5-7 7 7 7M5 12h16"/>',
    save: '<path d="M4 3h13l4 4v14H3V3ZM7 3v6h10V3M7 21v-8h10v8"/>',
    download: '<path d="M4 16v5h16v-5M12 3v13m-6-6 6 6 6-6"/>',
    trash: '<path d="M3 6h18M8 6V3h8v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
    cancel: '<path d="M5 5 19 19M5 19 19 5"/>',
    key: '<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9M16 16l3-3M19 19l3-3"/>',
    logout: '<path d="M9 3H3v18h6M9 12h12m-5-5 5 5-5 5"/>',
    tags: '<path d="M3 3h8l10 10-8 8L3 11Z"/><circle cx="7" cy="7" r="1"/>',
  };
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const svg = (name) =>
    `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.video}</svg>`;
  function icon(name, label, attrs = "") {
    return `<button type="button" class="icon" aria-label="${esc(label)}" title="${esc(label)}" data-tooltip="${esc(label)}" ${attrs}>${svg(name)}</button>`;
  }
  function link(name, label, href, active = false) {
    return `<a class="icon ${active ? "active" : ""}" href="${esc(href)}" ${active ? 'aria-current="page"' : ""} aria-label="${esc(label)}" title="${esc(label)}" data-tooltip="${esc(label)}">${svg(name)}</a>`;
  }
  function render(user, page) {
    const badge = document.getElementById('userBadge');
    if (badge) badge.textContent = '本地工作区';
    const nav = document.getElementById('navigation');
    if (nav) nav.innerHTML = link('video', '视频库', '/clinical/', true);
  }
  window.WorkspaceShell = Object.freeze({esc, svg, icon, link, render});
})();
