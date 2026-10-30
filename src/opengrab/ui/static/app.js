/* OpenGrab web UI — vanilla JS, talks to the local API. No build step. */
"use strict";

/* ============================================================ state */
const S = {
  links: new Map(),        // id -> link dict
  stats: null,
  token: "",
  filter: "all",
  expanded: new Set(),     // link ids with expanded children
  graph: [],               // [{t, bps}] last 60 s of aggregate speed
  conn: "connecting…",
  selected: new Set(),     // selected link ids (toolbar acts on these)
  focusId: null,           // row that has keyboard focus
  lastClicked: null,
};
let terminalJobId = null;
let terminalPollTimer = null;
let filesPath = "";
let packagizerCache = [];

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const BASE_PATH = String(window.OPENGRAB_BASE_PATH || "").replace(/\/$/, "");
const withBasePath = path => BASE_PATH + (path.startsWith("/") ? path : "/" + path);

/* ============================================================ utils */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function humanBytes(n) {
  if (n == null || isNaN(n)) return "—";
  const u = ["B", "kB", "MB", "GB", "TB"];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(v >= 100 || i === 0 ? 0 : 1) + " " + u[i];
}
function humanEta(s) {
  if (s == null || isNaN(s) || s < 0) return "—";
  s = Math.round(s);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m " + (s % 60) + "s";
  return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
}
function humanTime(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/* ============================================================ api */
function getToken() {
  const m = location.hash.match(/token=([A-Za-z0-9]+)/);
  if (m) { S.token = m[1]; localStorage.setItem("og_token", m[1]); }
  else S.token = localStorage.getItem("og_token") || "";
  return S.token;
}

async function api(path, method = "GET", body) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (S.token) headers["Authorization"] = "Bearer " + S.token;
  const res = await fetch(withBasePath(path), { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401) {
    showAuthBanner();
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    let msg = res.status + " " + res.statusText;
    try { msg = (await res.json()).error || msg; } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

function showAuthBanner() {
  $("#auth-banner").classList.remove("hidden");
}

/* ============================================================ SSE */
let sseSource = null, sseRetry = 0;
async function connectSSE() {
  if (sseSource) sseSource.close();
  // EventSource cannot send the Authorization header: trade the token for a
  // short-lived single-use ticket first.
  let url = withBasePath("/api/events");
  try {
    const t = await api("/api/events-ticket", "POST", {});
    url += "?ticket=" + encodeURIComponent(t.ticket);
  } catch (e) {
    S.conn = "reconnecting…"; setConnPill();
    if (e.message === "unauthorized") return;
    setTimeout(connectSSE, Math.min(15000, 500 * Math.pow(2, ++sseRetry)));
    return;
  }
  sseSource = new EventSource(url);
  sseSource.onopen = () => {
    sseRetry = 0;
    S.conn = "live";
    setConnPill();
  };
  sseSource.onerror = () => {
    S.conn = "reconnecting…";
    setConnPill();
    sseSource.close();
    const delay = Math.min(15000, 500 * Math.pow(2, ++sseRetry));
    setTimeout(connectSSE, delay);
  };
  sseSource.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    onEvent(msg.kind, msg.payload);
  };
}

function setUnread(n) {
  const b = $("#notif-badge");
  if (!b) return;
  b.textContent = n > 99 ? "99+" : String(n || 0);
  b.classList.toggle("hidden", !n);
}

function onEvent(kind, payload) {
  if (kind === "notification") {
    setUnread(payload.unread || 0);
    const item = payload.item;
    if (item && !["finished", "error"].includes(item.kind)) toast(item.title + (item.message ? ": " + item.message : ""), item.level === "failed" ? "err" : "");
    if ($("#tab-notifications")?.classList.contains("active")) renderNotifications();
    return;
  }
  if (kind === "addon") { loadAddons(); return; }
  if (kind === "linkgrabber") {
    if ($("#tab-linkgrabber")?.classList.contains("active")) renderLinkGrabber();
    return;
  }
  if (kind === "spider") {
    $("#sp-status").textContent = payload.status === "done"
      ? `Spider finished: ${payload.pages} page(s), ${payload.files} file link(s), ${payload.added} new.`
      : "Spider failed: " + (payload.error || "unknown error");
    renderLinkGrabber();
    return;
  }
  if (kind === "flow") { FLOW = payload; renderFlowChrome(); return; }
  if (kind === "when_done") { showWhenDone(payload); return; }
  if (kind === "captcha") {
    setCaptchaBadge(payload.pending || 0);
    if ($("#tab-captchas")?.classList.contains("active")) renderCaptchas();
    return;
  }
  if (payload && payload.id && payload.status) {
    const isNew = !S.links.has(payload.id);
    S.links.set(payload.id, payload);
    if (kind === "removed" || kind === "cleared") { /* stats payload; fallthrough */ }
    if (kind === "finished") toast("Download finished: " + (payload.title || payload.url), "ok");
    if (kind === "error") toast("Error: " + (payload.error || payload.url), "err");
    renderLists();
    updatePills();
  }
  if (payload && payload.counts) {
    S.stats = payload;
    pushGraph(payload.speed_bps || 0);
    updatePills();
    renderGraph();
  }
}

function setConnPill() {
  const p = $("#conn-pill");
  p.textContent = S.conn;
  p.className = "pill " + (S.conn === "live" ? "ok" : S.conn === "connecting…" ? "warn" : "err");
}

/* ============================================================ pills + graph */
function updatePills() {
  const s = S.stats || {};
  const bps = s.speed_bps || 0;
  $("#speed-pill").textContent = humanBytes(bps) + "/s";
  const active = (s.counts?.downloading || 0) + (s.counts?.resolving || 0) + (s.counts?.grabbing || 0);
  $("#active-pill").textContent = active + " active";
}

function pushGraph(bps) {
  S.graph.push({ t: Date.now(), bps });
  const cutoff = Date.now() - 61000;
  while (S.graph.length && S.graph[0].t < cutoff) S.graph.shift();
}

function renderGraph() {
  const cv = $("#speed-graph");
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  if (!w || !h) return;
  if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  // classic meter: dark field, faint grid, green trace
  ctx.strokeStyle = "rgba(120,170,230,.18)"; ctx.lineWidth = 1;
  for (let x = w; x > 0; x -= 10) { ctx.beginPath(); ctx.moveTo(x + .5, 0); ctx.lineTo(x + .5, h); ctx.stroke(); }
  if (S.graph.length < 2) return;
  const maxBps = Math.max(64 * 1024, ...S.graph.map(p => p.bps)) * 1.1;
  const now = Date.now();
  const x = p => w - ((now - p.t) / 60000) * w;
  const y = b => h - 1 - (b / maxBps) * (h - 3);
  ctx.beginPath();
  ctx.moveTo(x(S.graph[0]), h);
  for (const p of S.graph) ctx.lineTo(x(p), y(p.bps));
  ctx.lineTo(x(S.graph[S.graph.length - 1]), h);
  ctx.closePath();
  ctx.fillStyle = "rgba(38,160,51,.35)"; ctx.fill();
  ctx.beginPath();
  ctx.moveTo(x(S.graph[0]), y(S.graph[0].bps));
  for (const p of S.graph) ctx.lineTo(x(p), y(p.bps));
  ctx.strokeStyle = "#5fe05f"; ctx.lineWidth = 1.4; ctx.stroke();
}
setInterval(renderGraph, 1000);

/* ============================================================ link rows */
const ACTIVE = ["waiting", "resolving", "grabbing", "downloading", "paused",
                "captcha", "login", "paywall", "error"];

function rowActions(l) {
  // The toolbar acts on selected rows (IDM / JD2 style); rows only carry
  // buttons for states that need your input.
  const b = [];
  const sm = (txt, act, cls = "") =>
    `<button class="sm ${cls}" data-act="${act}" data-id="${l.id}">${txt}</button>`;
  switch (l.status) {
    case "error":
      b.push(sm("Retry", "start"));
      break;
    case "captcha":
      if (l.extra?.captcha_request_id) b.push(sm("Solve in browser", "captcha-browser-link"), sm("Retry", "captcha-retry"));
      break;
    case "paywall":
      b.push(sm("Try anyway", "paywall"));
      break;
  }
  if (l.plugin === "torrent" && ["downloading", "resolving", "paused"].includes(l.status)) b.push(sm("Files", "torrent-files"));
  return b.join("");
}

function childrenBlock(l) {
  if (!l.children || !l.children.length) return "";
  const open = S.expanded.has(l.id);
  const rows = open
    ? l.children.map((c, i) => {
        const [url, title] = Array.isArray(c) ? c : [c.url, c.title];
        return `<div class="kid"><span class="grow"><span class="t">${esc(title || url)}</span>
          <div class="u" style="font-size:11px">${esc(url)}</div></span>
          <button class="sm" data-act="queue-child" data-id="${l.id}" data-index="${i}">queue</button></div>`;
      }).join("")
    : "";
  return `<div class="kids">
    ${l.extra?.playlist_truncated ? `<div class="u" style="color:var(--warn)">Showing ${l.children.length} items; increase the playlist limit in Settings to enumerate more.</div>` : ""}
    <div class="u">${l.children.length} item(s) found. Nothing downloads until you queue a selection or choose “queue all”.</div>
    <button class="sm" data-act="toggle-kids" data-id="${l.id}">${open ? "▾ hide" : "▸ show " + l.children.length + " children"}</button>
    ${open ? `<button class="sm" data-act="queue-children" data-id="${l.id}">queue all</button>` : ""}
    ${rows}
  </div>`;
}

const STATUS_TEXT = {
  waiting: "Queued", resolving: "Checking link", grabbing: "Checking link",
  downloading: "Downloading", paused: "Paused", finished: "Complete", error: "Error",
  captcha: "Needs captcha", login: "Needs login", paywall: "Paywall", skipped: "Skipped",
};

function statusText(l) {
  if (l.status === "downloading" && l.size) return "Downloading " + Math.round((l.progress || 0) * 100) + "%";
  if (l.status === "error" && /^retry \d+\/\d+/.test(l.error || "")) return "Retrying";
  return STATUS_TEXT[l.status] || l.status;
}

function linkRow(l) {
  const pct = Math.round((l.progress || 0) * 100);
  const barCls = (l.status === "error" ? "bar err" : l.status === "paused" ? "bar paused" : "bar") + (pct >= 55 ? " hi" : "");
  const sel = S.selected.has(l.id) ? " selected" : "";
  const notes = [];
  if (l.status === "error" && l.error) notes.push(`<div class="u" style="color:var(--err)">${esc(l.error)}</div>`);
  if (l.status === "captcha" && l.extra?.captcha_request_id) notes.push(`<div class="u" style="color:var(--warn)">Waiting for a ${esc(l.captcha_kind)} check on ${esc((l.extra.captcha_site_url || l.url).split("/")[2] || "")}${l.extra.captcha_ray_id ? ", Ray ID " + esc(l.extra.captcha_ray_id) : ""}. Open Captchas to solve it.</div>`);
  if (l.extra?.avscan && l.extra.avscan.status !== "skipped") notes.push(l.extra.avscan.status === "infected"
    ? `<div class="u hashbad">Virus scan: infected${l.extra.avscan.quarantined ? ", moved to quarantine" : ""}</div>`
    : `<div class="u ${l.extra.avscan.status === "clean" ? "hashok" : ""}">Virus scan: ${esc(l.extra.avscan.status)}</div>`);
  if (l.plugin === "torrent" && l.extra?.torrent && l.status !== "error") {
    const t = l.extra.torrent;
    notes.push(`<div class="u">Torrent: ${t.peers || 0} peer(s), ${t.seeds || 0} seed(s)${t.ratio ? ", ratio " + t.ratio : ""}${t.seeding === "seeding" ? ", seeding" : ""}</div>`);
  }
  if (l.extra?.hash) notes.push(l.extra.hash.ok ? `<div class="u hashok">${esc(l.extra.hash.algo)} checksum OK</div>` : `<div class="u hashbad">Checksum ${l.extra.hash.error ? esc(l.extra.hash.error) : "mismatch"}</div>`);
  if (l.status === "paywall" && l.paywall_note) notes.push(`<div class="u" style="color:var(--warn)">${esc(l.paywall_note)}</div>`);
  if (l.extra?.archive_extraction) notes.push(`<div class="u" style="color:${l.extra.archive_extraction.status === "extracted" ? "var(--ok-dark)" : "var(--warn)"}">Archive ${esc(l.extra.archive_extraction.status)}${l.extra.archive_extraction.message ? ": " + esc(l.extra.archive_extraction.message) : ""}</div>`);
  const sub = l.status === "finished" && l.path ? l.path : (l.url + (l.plugin ? " · " + l.plugin : ""));
  return `<div class="linkrow${sel}" data-lid="${l.id}" data-status="${esc(l.status)}" title="${esc(l.url)}"
    role="row" tabindex="${S.focusId === l.id ? 0 : -1}" aria-selected="${S.selected.has(l.id)}">
    <div class="c-title"><span class="sg ${esc(l.status)}"></span><span class="names">
      <div class="t">${esc(l.title || l.filename || l.url)}</div>
      <div class="u">${esc(sub)}</div>${notes.join("")}</span></div>
    <div class="c-size">${l.size ? humanBytes(l.size) : "—"}</div>
    <div class="c-status"><span class="st ${esc(l.status)}">${esc(statusText(l))}</span>${l.status === "waiting" && l.extra?.wait_reason ? `<span class="why" title="${esc(l.extra.wait_reason)}">${esc(l.extra.wait_reason)}</span>` : ""}</div>
    <div class="c-eta">${l.status === "downloading" ? humanEta(l.eta) : (l.status === "finished" ? humanTime(l.finished) : "")}</div>
    <div class="c-speed">${l.status === "downloading" ? humanBytes(l.speed) + "/s" : ""}</div>
    <div class="c-prog"><div class="${barCls}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i><span class="pct">${pct}%</span></div></div>
    <div class="c-actions">${rowActions(l)}</div>
    ${(l.status === "captcha" && !l.extra?.captcha_request_id) || l.status === "login" ? `<span class="inlineform" data-for="${l.id}"></span>` : ""}
    ${childrenBlock(l)}
  </div>`;
}

/* inline parked-state forms (captcha / login) */
function renderInlineForms(root) {
  $$(".inlineform", root).forEach(async (el) => {
    const id = el.dataset.for;
    if (el.dataset.ready) return;
    el.dataset.ready = "1";
    const l = S.links.get(id);
    if (!l) return;
    if (l.status === "captcha") {
      let img = "";
      if (l.has_captcha_image) {
        try {
          const r = await api(`/api/links/${id}/captcha-image`);
          img = `<img src="data:image/png;base64,${r.image}" alt="captcha">`;
        } catch { img = "<span class='hint'>captcha image unavailable</span>"; }
      }
      el.innerHTML = img +
        `<span>Enter the code${l.captcha_kind !== "text" ? " (kind: " + esc(l.captcha_kind) + ")" : ""}:</span>
         <input data-captcha-input="${id}" type="text" placeholder="captcha code" style="flex:0 0 160px">
         <button class="sm primary" data-act="submit-captcha" data-id="${id}">submit</button>`;
    } else if (l.status === "login") {
      el.innerHTML = `<span>${l.login_hint ? esc(l.login_hint) : "This link needs a login:"}</span>
        <input data-login-user="${id}" type="text" placeholder="username">
        <input data-login-pass="${id}" type="password" placeholder="password">
        <button class="sm primary" data-act="submit-login" data-id="${id}">login</button>`;
    }
  });
}

/* ============================================================ rendering */
function queueLinks() {
  return Array.from(S.links.values())
    .filter(l => ACTIVE.includes(l.status))
    .sort((a, b) => (b.priority - a.priority) || (a.created - b.created));
}

function allLinksFiltered() {
  let arr = Array.from(S.links.values());
  switch (S.filter) {
    case "active": arr = arr.filter(l => ACTIVE.includes(l.status)); break;
    case "finished": arr = arr.filter(l => l.status === "finished"); break;
    case "failed": arr = arr.filter(l => ["error", "skipped"].includes(l.status)); break;
    case "parked": arr = arr.filter(l => ["captcha", "login", "paywall"].includes(l.status)); break;
    default:
      if (S.filter.startsWith("queue:")) arr = arr.filter(l => l.extra?.queue === S.filter.slice(6));
  }
  return arr.sort((a, b) => b.created - a.created);
}

function renderLists() {
  // drop selections for links that are gone
  for (const id of [...S.selected]) if (!S.links.has(id)) S.selected.delete(id);
  const q = $("#queue-list");
  const ql = queueLinks();
  q.innerHTML = ql.map(l => linkRow(l)).join("");
  $("#queue-empty").classList.toggle("hidden", ql.length > 0);
  const a = $("#all-list");
  const al = allLinksFiltered();
  a.innerHTML = al.map(l => linkRow(l)).join("");
  $("#all-empty").classList.toggle("hidden", al.length > 0);
  renderInlineForms(document);
  renderAccounts();
  renderFilters();
  renderCounts();
  updateToolbar();
  renderFlowChrome();
  restoreRowFocus();
}

/* keyboard: arrows move, Space selects, Enter resumes/starts, Home/End */
function restoreRowFocus() {
  if (!S.focusId || !document.activeElement?.classList?.contains("linkrow") && !S.keyboard) return;
  const row = document.querySelector(`.listpane.active .linkrow[data-lid="${S.focusId}"]`);
  if (row && document.activeElement !== row) row.focus({ preventScroll: true });
}
document.addEventListener("keydown", (ev) => {
  const row = ev.target.closest?.(".linkrow");
  if (!row) return;
  const rows = $$(".listpane.active .linkrow");
  const i = rows.indexOf(row);
  let next = null;
  if (ev.key === "ArrowDown") next = rows[i + 1];
  else if (ev.key === "ArrowUp") next = rows[i - 1];
  else if (ev.key === "Home") next = rows[0];
  else if (ev.key === "End") next = rows[rows.length - 1];
  else if (ev.key === " ") {
    ev.preventDefault();
    S.selected.has(row.dataset.lid) ? S.selected.delete(row.dataset.lid) : S.selected.add(row.dataset.lid);
    S.focusId = row.dataset.lid; S.keyboard = true; renderLists(); return;
  } else if (ev.key === "Enter") {
    ev.preventDefault();
    if (!S.selected.size) S.selected.add(row.dataset.lid);
    $("#tb-resume").click(); return;
  } else return;
  ev.preventDefault();
  if (next) {
    S.focusId = next.dataset.lid; S.keyboard = true;
    if (!ev.shiftKey && !ev.ctrlKey) { S.selected.clear(); }
    S.selected.add(next.dataset.lid);
    renderLists();
  }
});
document.addEventListener("focusin", (ev) => {
  const row = ev.target.closest?.(".linkrow");
  if (row) S.focusId = row.dataset.lid;
});

function renderCounts() {
  const arr = Array.from(S.links.values());
  const n = (pred) => arr.filter(pred).length;
  const set = (id, v) => { const el = $("#" + id); if (el) el.textContent = v ? String(v) : ""; };
  set("cnt-all", arr.length);
  set("cnt-queue", n(l => ACTIVE.includes(l.status)));
  set("cnt-finished", n(l => l.status === "finished"));
  set("cnt-failed", n(l => ["error", "skipped"].includes(l.status)));
  set("cnt-parked", n(l => ["captcha", "login", "paywall"].includes(l.status)));
}

/* ============================================================ selection + toolbar */
function selectedLinks() {
  return [...S.selected].map(id => S.links.get(id)).filter(Boolean);
}

function updateToolbar() {
  const sel = selectedLinks();
  const any = (st) => sel.some(l => st.includes(l.status));
  $("#tb-resume").disabled = !any(["paused", "waiting", "error"]);
  $("#tb-pause").disabled = !any(["downloading", "waiting"]);
  $("#tb-stop").disabled = !any(["downloading", "waiting", "resolving", "grabbing"]);
  $("#tb-delete").disabled = sel.length === 0;
}

function visibleRowIds(row) {
  const list = row.closest(".linklist");
  return list ? $$(".linkrow", list).map(r => r.dataset.lid) : [];
}

document.addEventListener("click", (ev) => {
  const row = ev.target.closest(".linkrow");
  if (!row || ev.target.closest("button, input, a, select, textarea, .kids")) return;
  const id = row.dataset.lid;
  if (ev.shiftKey && S.lastClicked) {
    const ids = visibleRowIds(row);
    const [a, b] = [ids.indexOf(S.lastClicked), ids.indexOf(id)].sort((x, y) => x - y);
    if (a >= 0 && b >= 0) { if (!ev.ctrlKey) S.selected.clear(); ids.slice(a, b + 1).forEach(i => S.selected.add(i)); }
  } else if (ev.ctrlKey || ev.metaKey) {
    S.selected.has(id) ? S.selected.delete(id) : S.selected.add(id);
    S.lastClicked = id;
  } else {
    S.selected.clear(); S.selected.add(id); S.lastClicked = id;
  }
  S.focusId = id; S.keyboard = false;
  renderLists();
});

async function forEachLink(links, path) {
  for (const l of links) {
    try { await api(`/api/links/${l.id}/${path}`, "POST"); } catch (e) { if (e.message === "unauthorized") return; }
  }
  refreshData();
}

$("#tb-add").addEventListener("click", () => openAddDialog());
$("#tb-resume").addEventListener("click", async () => {
  const sel = selectedLinks();
  await forEachLink(sel.filter(l => l.status === "paused"), "resume");
  await forEachLink(sel.filter(l => ["waiting", "error"].includes(l.status)), "start");
});
$("#tb-pause").addEventListener("click", () =>
  forEachLink(selectedLinks().filter(l => ["downloading", "waiting"].includes(l.status)), "pause"));
$("#tb-stop").addEventListener("click", () =>
  forEachLink(selectedLinks().filter(l => ["downloading", "waiting", "resolving", "grabbing"].includes(l.status)), "stop"));
$("#tb-delete").addEventListener("click", () => removeSelected());
$("#tb-startall").addEventListener("click", async () => {
  const all = Array.from(S.links.values());
  await forEachLink(all.filter(l => l.status === "paused"), "resume");
  await forEachLink(all.filter(l => l.status === "waiting"), "start");
});
$("#tb-pauseall").addEventListener("click", () =>
  forEachLink(Array.from(S.links.values()).filter(l => ["downloading", "waiting"].includes(l.status)), "pause"));
$("#tb-captchas").addEventListener("click", () => $('.tab[data-tab="captchas"]').click());
$("#tb-settings").addEventListener("click", () => $('.tab[data-tab="settings"]').click());

async function removeSelected() {
  const sel = selectedLinks();
  if (!sel.length) return;
  if (!confirm(sel.length === 1 ? "Remove this download from the list? The file stays on disk."
                                        : `Remove ${sel.length} downloads from the list? Files stay on disk.`)) return;
  await forEachLink(sel, "remove");
  S.selected.clear();
}

/* ============================================================ add links dialog */
function openAddDialog(text = "") {
  const d = $("#add-dialog");
  if (text) $("#url-input").value = ($("#url-input").value ? $("#url-input").value + "\n" : "") + text;
  if (!d.open) d.showModal();
  $("#url-input").focus();
}
$("#add-close").addEventListener("click", () => $("#add-dialog").close());
$("#add-cancel").addEventListener("click", () => $("#add-dialog").close());

document.addEventListener("keydown", (ev) => {
  const typing = ev.target.closest("input, textarea, select, [contenteditable]");
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "n") { ev.preventDefault(); openAddDialog(); }
  else if (ev.key === "Delete" && !typing && S.selected.size) { ev.preventDefault(); removeSelected(); }
  else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "a" && !typing && $(".listpane.active")) {
    ev.preventDefault();
    $$(".listpane.active .linkrow").forEach(r => S.selected.add(r.dataset.lid));
    renderLists();
  }
});
document.addEventListener("paste", (ev) => {
  if (ev.target.closest("input, textarea, [contenteditable]")) return;
  const text = ev.clipboardData?.getData("text/plain") || "";
  const links = text.split(/\s+/).filter(t => /^(https?:\/\/|magnet:\?)/i.test(t));
  if (links.length) { ev.preventDefault(); openAddDialog(links.join("\n")); }
});

let accountsCache = [], filtersCache = [], usageCache = {}, editingAccount = null;
async function renderAccounts() {
  if (!accountsCache.length && !$("#tab-accounts").classList.contains("active")) return;
  const el = $("#accounts-list");
  el.innerHTML = accountsCache.length
    ? accountsCache.map(a => {
        const u = usageCache[a.id] || {};
        const limit = u.daily_bytes ? ` of ${humanBytes(u.daily_bytes)}` : "";
        return `<div class="krow">
        <code>${esc(a.site)}</code>
        <span>${esc(a.username)}</span>
        ${a.premium ? '<span class="badge include">premium</span>' : ""}
        ${u.service ? `<span class="badge">${esc(u.service === "realdebrid" ? "Real-Debrid" : "AllDebrid")}</span>` : ""}
        ${a.has_cookies ? '<span class="badge">cookies</span>' : ""}
        <span class="grow hint">${u.used_today != null ? "Today " + humanBytes(u.used_today) + limit : ""}${u.active ? `, ${u.active} running` : ""}</span>
        <button class="sm" data-act="acc-edit" data-id="${esc(a.id)}">Edit</button>
        <button class="sm danger" data-act="acc-del" data-id="${esc(a.id)}">Remove</button>
      </div>`; }).join("")
    : '<div class="empty">No accounts stored.</div>';
}
async function renderFilters() {
  if (!filtersCache.length && !$("#tab-filters").classList.contains("active")) return;
  const el = $("#filters-list");
  el.innerHTML = filtersCache.length
    ? filtersCache.map(f => `<div class="krow">
        <span class="badge ${esc(f.kind)}">${esc(f.kind)}</span>
        <code>${esc(f.pattern)}</code>
        <span style="color:var(--fg-dim);font-size:12px">${f.use_glob ? "glob" : "regex"}</span>
        <span class="grow"></span>
        <button class="sm danger" data-act="flt-del" data-id="${esc(f.id)}">remove</button>
      </div>`).join("")
    : '<div class="empty">No filters.</div>';
}

async function renderLinkGrabber() {
  const el = $("#linkgrabber-list");
  if (!el) return;
  try {
    const candidates = await api("/api/linkgrabber");
    const status = await api("/api/clipboard/status");
    $("#clipboard-state").textContent = {
      off: "Clipboard monitoring is off.",
      watching: "Clipboard monitoring is on; copied links wait here for your approval.",
      unavailable: "Clipboard monitoring is on, but the desktop clipboard is unavailable.",
    }[status.status] || "Clipboard status unknown.";
    const STATE = { online: "Online", offline: "Offline", unknown: "Unknown", unchecked: "Checking…" };
    const FROM = { clipboard: "Copied", paste: "Pasted", spider: "Spider", clicknload: "Click'n'Load" };
    el.innerHTML = candidates.length
      ? candidates.map(item => `<div class="lgrow">
          <span title="${esc(item.url)}">${esc(item.filename || item.url.split("/").pop() || item.url)}<div class="u">${esc(item.url)}</div></span>
          <span>${item.size != null ? humanBytes(item.size) : "—"}</span>
          <span class="lgstate ${esc(item.state || "unchecked")}">${STATE[item.state] || "Not checked"}</span>
          <span>${esc(FROM[item.source] || "Copied")}${item.package ? `<div class="u">${esc(item.package)}</div>` : ""}${(item.passwords || []).length ? `<div class="u">${item.passwords.length} password(s)</div>` : ""}</span>
          <span><button class="sm primary" data-act="candidate-add" data-id="${esc(item.id)}">Add</button>
          <button class="sm" data-act="candidate-dismiss" data-id="${esc(item.id)}">Dismiss</button></span>
        </div>`).join("")
      : '<div class="empty">Nothing waiting for review. Paste links above, start the spider, or copy links with monitoring on.</div>';
    try {
      const cnl = await api("/api/cnl/status");
      $("#cnl-state").textContent = cnl.listening ? `Click'n'Load is listening on port ${cnl.port}.`
        : cnl.error ? "Click'n'Load is off: " + cnl.error : "Click'n'Load is off.";
    } catch {}
  } catch { /* api() handles authorization feedback */ }
}

function refreshData() {
  api("/api/links").then(links => {
    S.links = new Map(links.map(l => [l.id, l]));
    renderLists();
    updatePills();
  }).catch(() => {});
  api("/api/stats").then(s => { S.stats = s; updatePills(); }).catch(() => {});
  api("/api/accounts").then(a => { accountsCache = a; renderAccounts(); }).catch(() => {});
  if ($("#tab-accounts")?.classList.contains("active"))
    api("/api/accounts/usage").then(r => { usageCache = Object.fromEntries(r.accounts.map(u => [u.id, u])); renderAccounts(); }).catch(() => {});
  api("/api/filters").then(f => { filtersCache = f; renderFilters(); }).catch(() => {});
  if ($("#tab-linkgrabber")?.classList.contains("active")) renderLinkGrabber();
  if ($("#tab-notifications")?.classList.contains("active")) renderNotifications();
  if ($("#tab-files")?.classList.contains("active")) renderFiles();
  if ($("#tab-packagizer")?.classList.contains("active")) renderPackagizer();
}

/* ============================================================ captchas */
function setCaptchaBadge(n) {
  const b = $("#captcha-badge");
  if (!b) return;
  b.textContent = String(n || 0);
  b.classList.toggle("hidden", !n);
}
async function renderCaptchas() {
  try {
    const r = await api("/api/captcha/inbox");
    const pend = r.pending || [];
    setCaptchaBadge(pend.length);
    const el = $("#captcha-list");
    if (el) el.innerHTML = pend.length ? pend.map(c => {
      const left = Math.max(0, Math.round((c.expires_at * 1000 - Date.now()) / 60000));
      const links = (c.links || []).map(x => esc(x.title)).join(", ");
      return `<div class="krow"><span class="st captcha">${esc(c.kind)}</span>
        <span class="grow"><b>${esc(c.host)}</b> · ${c.links.length} download(s)${links ? ": " + links : ""}
        <div class="u">expires in ${left} min</div></span>
        ${c.solve_in_browser ? `<button class="sm primary" data-act="captcha-browser" data-id="${c.id}" ${c.browser_busy ? "disabled" : ""}>${c.browser_busy ? "browser open…" : "solve in browser"}</button>` : ""}
        ${c.harvest ? `<button class="sm" data-act="captcha-harvest" data-id="${c.id}">solver page</button>` : ""}
        <button class="sm danger" data-act="captcha-skip" data-id="${c.id}">skip</button></div>`;
    }).join("") : '<div class="empty">Nothing is waiting for a captcha.</div>';
    const cl = $("#clearance-list");
    if (cl) cl.innerHTML = (r.clearances || []).length ? r.clearances.map(c =>
      `<div class="krow"><code>${esc(c.host)}</code><span class="grow hint">valid until ${new Date(c.expires_at * 1000).toLocaleTimeString()}</span>
       <button class="sm danger" data-act="clearance-del" data-host="${esc(c.host)}">forget</button></div>`).join("")
      : '<div class="empty">No saved clearances.</div>';
  } catch {}
}

async function renderNotifications() {
  try {
    const items = await api("/api/notifications");
    setUnread(items.filter(n => !n.read).length);
    $("#notifications-list").innerHTML = items.length ? items.map(n =>
      `<div class="krow${n.read ? "" : " unread"}"><span class="st ${esc(n.kind || "")}">${esc(n.kind || "notice")}</span><span class="grow"><b>${esc(n.title || "")}</b> ${esc(n.message || n.text || "")}</span><span class="hint">${n.timestamp ? new Date(n.timestamp * 1000).toLocaleString() : ""}</span>${n.read ? "" : `<button class="sm" data-act="notif-read" data-id="${n.id}">mark read</button>`}</div>`
    ).join("") : '<div class="empty">No recent notifications.</div>';
  } catch {}
}
async function refreshUnread() {
  try { setUnread((await api("/api/notifications/summary")).unread); } catch {}
}

async function renderFiles() {
  try {
    const data = await api("/api/files?path=" + encodeURIComponent(filesPath));
    filesPath = data.path || "";
    $("#files-path").textContent = "/" + filesPath;
    $("#btn-files-up").disabled = !filesPath;
    $("#files-list").innerHTML = data.items.length ? data.items.map(item => {
      const icon = item.type === "directory" ? "📁" : "📄";
      const actions = item.type === "directory"
        ? `<button class="sm" data-act="file-open" data-path="${esc(item.path)}">Open</button>`
        : `<button class="sm" data-act="file-audio" data-path="${esc(item.path)}">Play</button><button class="sm" data-act="file-download" data-path="${esc(item.path)}">Download</button>`;
      return `<div class="krow"><span>${icon}</span><code class="file-name">${esc(item.name)}</code><span class="hint">${item.type === "file" ? humanBytes(item.size) : "folder"}</span><span class="grow"></span>${actions}<button class="sm" data-act="file-rename" data-path="${esc(item.path)}" data-name="${esc(item.name)}">Rename</button><button class="sm danger" data-act="file-delete" data-path="${esc(item.path)}">Delete</button></div>`;
    }).join("") : '<div class="empty">This folder is empty.</div>';
  } catch (e) { $("#files-list").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function renderPackagizer() {
  try {
    packagizerCache = await api("/api/packagizer");
    $("#packagizer-list").innerHTML = packagizerCache.length
      ? packagizerCache.map(rule => `<div class="krow">
          <span class="badge">${esc(rule.name || "rule")}</span>
          <code>${esc(rule.pattern_type)}: ${esc(rule.pattern)}</code>
          <span class="grow">${esc(rule.target_subdir || "")}${rule.filename_template ? " → " + esc(rule.filename_template) : ""}</span>
          <button class="sm danger" data-act="pack-del" data-id="${esc(rule.id)}">remove</button>
        </div>`).join("")
      : '<div class="empty">No Packagizer rules. Matching rules are applied in order.</div>';
  } catch {}
}

function childPath(name) { return filesPath ? filesPath + "/" + name : name; }

/* ============================================================ actions */
async function act(action, el) {
  const id = el.dataset.id;
  try {
    switch (action) {
      case "start": await api(`/api/links/${id}/start`, "POST"); break;
      case "pause": await api(`/api/links/${id}/pause`, "POST"); break;
      case "resume": await api(`/api/links/${id}/resume`, "POST"); break;
      case "stop":
        if (confirm("Stop this download?")) await api(`/api/links/${id}/stop`, "POST");
        break;
      case "remove": {
        if (!confirm("Remove this link?")) return;
        await api(`/api/links/${id}/remove`, "POST");
        break;
      }
      case "candidate-add":
        await api(`/api/linkgrabber/${id}/add`, "POST", {});
        toast("Link added to the queue", "ok");
        break;
      case "candidate-dismiss":
        await api(`/api/linkgrabber/${id}/dismiss`, "POST", {});
        break;
      case "file-open": filesPath = el.dataset.path; await renderFiles(); return;
      case "file-download": {
        const url = withBasePath("/api/files/download") + "?path=" + encodeURIComponent(el.dataset.path);
        const response = await fetch(url, { headers: S.token ? { Authorization: "Bearer " + S.token } : {} });
        if (!response.ok) throw new Error("download failed: " + response.status);
        const blob = await response.blob(), link = document.createElement("a");
        link.href = URL.createObjectURL(blob); link.download = el.dataset.path.split("/").pop(); link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000); return;
      }
      case "file-audio": {
        const ticket = await api("/api/audio-ticket", "POST", { path: el.dataset.path });
        const player = $("#audio-player"); player.src = ticket.url; player.hidden = false; await player.play(); return;
      }
      case "file-delete":
        if (!confirm("Delete " + el.dataset.path + "? (folders must be empty)")) return;
        await api("/api/files", "POST", { action: "delete", path: el.dataset.path });
        await renderFiles(); return;
      case "file-rename": {
        const name = prompt("New name", el.dataset.name); if (!name || name === el.dataset.name) return;
        const parent = el.dataset.path.includes("/") ? el.dataset.path.slice(0, el.dataset.path.lastIndexOf("/") + 1) : "";
        await api("/api/files", "POST", { action: "rename", path: el.dataset.path, to: parent + name });
        await renderFiles(); return;
      }
      case "paywall": await api(`/api/links/${id}/paywall-accept`, "POST"); break;
      case "queue-child":
        await api(`/api/links/${id}/queue-child`, "POST", { index: +el.dataset.index });
        break;
      case "queue-children": await api(`/api/links/${id}/queue-children`, "POST"); break;
      case "toggle-kids":
        S.expanded.has(id) ? S.expanded.delete(id) : S.expanded.add(id);
        renderLists();
        break;
      case "submit-captcha": {
        const inp = $(`[data-captcha-input="${id}"]`);
        if (!inp?.value.trim()) return toast("Type the captcha code first", "err");
        await api(`/api/links/${id}/captcha`, "POST", { code: inp.value.trim() });
        break;
      }
      case "submit-login": {
        const u = $(`[data-login-user="${id}"]`)?.value || "";
        const p = $(`[data-login-pass="${id}"]`)?.value || "";
        if (!u && !p) return toast("Enter username or password", "err");
        await api(`/api/links/${id}/login`, "POST", { username: u, password: p });
        break;
      }
      case "acc-del": {
        if (!confirm("Remove this account?")) return;
        await api(`/api/accounts/${encodeURIComponent(id)}`, "DELETE");  // by id (was by site: B6)
        accountsCache = [];
        refreshData();
        break;
      }
      case "acc-edit": {
        const a = accountsCache.find(x => x.id === id);
        if (!a) return;
        editingAccount = id;
        $("#acc-site").value = a.site; $("#acc-site").disabled = true;
        $("#acc-user").value = a.username; $("#acc-user").disabled = true;
        $("#acc-pass").value = ""; $("#acc-token").value = ""; $("#acc-cookies").value = "";
        $("#acc-debrid").value = a.data?.debrid || "";
        $("#acc-premium").checked = !!a.premium;
        $("#acc-daily").value = Math.round((a.data?.usage?.daily_bytes || 0) / 1048576);
        $("#acc-parallel").value = a.data?.usage?.max_parallel || 0;
        $("#btn-acc-add").textContent = "Save changes";
        $("#btn-acc-cancel").hidden = false;
        return;
      }
      case "flt-del": {
        await api(`/api/filters/${encodeURIComponent(el.dataset.id)}`, "DELETE");
        filtersCache = [];
        refreshData();
        break;
      }
      case "captcha-browser":
        await api(`/api/captcha/${id}/browser`, "POST", {});
        toast("Opening the site in a browser window. Pass the check there.", "ok");
        await renderCaptchas();
        return;
      case "captcha-browser-link": {
        const l = S.links.get(id);
        const rid = l?.extra?.captcha_request_id;
        if (rid) { await api(`/api/captcha/${rid}/browser`, "POST", {}); toast("Opening the site in a browser window.", "ok"); }
        return;
      }
      case "captcha-harvest": {
        const r = await api(`/api/captcha/${id}/harvest-link`, "POST", {});
        window.open(withBasePath(r.url.replace(BASE_PATH, "")), "_blank", "noopener,width=480,height=640");
        toast(r.note, "");
        return;
      }
      case "captcha-skip":
        if (!confirm("Skip this captcha? Waiting downloads will be marked as failed.")) return;
        await api(`/api/captcha/${id}/skip`, "POST", {});
        await renderCaptchas();
        break;
      case "captcha-retry": await api(`/api/links/${id}/captcha-retry`, "POST", {}); break;
      case "clearance-del":
        await api(`/api/captcha/clearances/${encodeURIComponent(el.dataset.host)}`, "DELETE");
        await renderCaptchas();
        return;
      case "torrent-files": await openTorrentFiles(id); return;
      case "notif-read":
        await api(`/api/notifications/${encodeURIComponent(id)}/read`, "POST", {});
        await renderNotifications();
        return;
      case "pack-del":
        await api(`/api/packagizer/${encodeURIComponent(id)}`, "DELETE");
        await renderPackagizer();
        return;
    }
    refreshData();
  } catch (e) {
    if (e.message !== "unauthorized") toast("Action failed: " + e.message, "err");
  }
}

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (btn) act(btn.dataset.act, btn);
});

/* ============================================================ tabs */
document.addEventListener("click", (ev) => {
  const t = ev.target.closest(".tab[data-tab]");
  if (!t) return;
  $$(".tab").forEach(x => x.classList.remove("active"));
  $$(".tabpane").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("#tab-" + t.dataset.tab).classList.add("active");
  if (t.dataset.filter) { S.filter = t.dataset.filter; renderLists(); }
  if (t.dataset.tab === "settings") loadSettings();
  if (t.dataset.tab === "linkgrabber") renderLinkGrabber();
  if (t.dataset.tab === "notifications") renderNotifications();
  if (t.dataset.tab === "captchas") renderCaptchas();
  if (t.dataset.tab === "accounts") renderLogins();
  if (t.dataset.tab === "plugins") renderPluginsPage();
  if (t.dataset.tab === "files") renderFiles();
  if (t.dataset.tab === "packagizer") renderPackagizer();
  refreshData();
  renderGraph();
});

$$(".chip").forEach(c => c.addEventListener("click", () => {
  $$(".chip").forEach(x => x.classList.remove("active"));
  c.classList.add("active");
  S.filter = c.dataset.f;
  renderLists();
}));

/* ============================================================ add urls */
async function addUrls() {
  const ta = $("#url-input");
  const urls = ta.value.split("\n").map(s => s.trim()).filter(Boolean);
  if (!urls.length) return;
  if ($("#url-review")?.checked) {
    try {
      const r = await api("/api/linkgrabber", "POST", { urls, source: "paste" });
      toast(`${r.added} link(s) wait in the LinkGrabber`, "ok");
      ta.value = "";
      if ($("#add-dialog").open) $("#add-dialog").close();
      $('.tab[data-tab="linkgrabber"]').click();
    } catch (e) { toast("Add failed: " + e.message, "err"); }
    return;
  }
  try {
    const out = await api("/api/urls", "POST", {
      urls,
      priority: +$("#url-priority").value || 0,
      auto_download: $("#url-auto").checked,
      queue: $("#url-queue")?.value || "",
      hash: $("#url-hash")?.value.trim() || "",
    });
    if ($("#url-hash")) $("#url-hash").value = "";
    const errs = (out || []).filter(o => o.error);
    if (errs.length) toast(errs.length + " link(s) failed: " + errs[0].error, "err");
    else toast("Added " + urls.length + " link(s)", "ok");
    ta.value = "";
    if ($("#add-dialog").open) $("#add-dialog").close();
    refreshData();
  } catch (e) {
    if (e.message !== "unauthorized") toast("Add failed: " + e.message, "err");
  }
}
$("#btn-add").addEventListener("click", addUrls);
$("#url-input").addEventListener("keydown", e => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) addUrls();
});

/* ============================================================ drag & drop */
const dz = $("#dropzone");
let dragDepth = 0;
function dzOn(e, show) {
  dz.classList.toggle("dragging", show);
  let hint = $("#drop-hint");
  if (!hint) {
    hint = document.createElement("div");
    hint.id = "drop-hint";
    hint.className = "drophp";
    hint.textContent = "release to add links";
    dz.appendChild(hint);
  }
  hint.classList.toggle("hidden", !show);
}
window.addEventListener("dragenter", e => { e.preventDefault(); dragDepth++; dzOn(e, true); });
window.addEventListener("dragover", e => { e.preventDefault(); });
window.addEventListener("dragleave", e => {
  e.preventDefault();
  if (--dragDepth <= 0) { dragDepth = 0; dzOn(e, false); }
});
window.addEventListener("drop", async e => {
  e.preventDefault();
  dragDepth = 0; dzOn(e, false);
  const ta = $("#url-input");
  const text = e.dataTransfer?.getData("text/plain") || "";
  const urls = text.split("\n").map(s => s.trim()).filter(Boolean);
  if (urls.length) {
    ta.value = (ta.value ? ta.value + "\n" : "") + urls.join("\n");
    await addUrls();
  }
});

/* ============================================================ accounts */
function resetAccountForm() {
  editingAccount = null;
  for (const id of ["#acc-site", "#acc-user", "#acc-pass", "#acc-token", "#acc-cookies"]) { $(id).value = ""; $(id).disabled = false; }
  $("#acc-debrid").value = ""; $("#acc-premium").checked = false;
  $("#acc-daily").value = 0; $("#acc-parallel").value = 0;
  $("#btn-acc-add").textContent = "Add account";
  $("#btn-acc-cancel").hidden = true;
}
$("#btn-acc-cancel").addEventListener("click", resetAccountForm);
$("#btn-acc-add").addEventListener("click", async () => {
  const site = $("#acc-site").value.trim();
  const user = $("#acc-user").value.trim();
  if (!site || !user) return toast("Site and user name are required", "err");
  const body = { site, username: user, password: $("#acc-pass").value, premium: $("#acc-premium").checked,
                 debrid: $("#acc-debrid").value,
                 usage: { daily_mb: +$("#acc-daily").value || 0, max_parallel: +$("#acc-parallel").value || 0 } };
  if ($("#acc-token").value) body.api_token = $("#acc-token").value;
  const cookies = $("#acc-cookies").value.trim();
  if (cookies) body[cookies.includes("\t") ? "cookies_txt" : "cookie_header"] = cookies;
  try {
    if (editingAccount) await api(`/api/accounts/${encodeURIComponent(editingAccount)}`, "POST", body);
    else await api("/api/accounts", "POST", body);
    resetAccountForm();
    toast("Account saved", "ok");
    accountsCache = [];
    refreshData();
  } catch (e) { toast("Could not save the account: " + e.message, "err"); }
});

/* ============================================================ filters */
$("#btn-flt-add").addEventListener("click", async () => {
  const pattern = $("#flt-pattern").value.trim();
  if (!pattern) return toast("Pattern required", "err");
  try {
    await api("/api/filters", "POST", {
      pattern,
      kind: $("#flt-kind").value,
      use_glob: $("#flt-type").value === "glob",
    });
    $("#flt-pattern").value = "";
    toast("Filter added", "ok");
    refreshData();
  } catch (e) { toast("Failed: " + e.message, "err"); }
});

$("#btn-clear-finished").addEventListener("click", async () => {
  if (!confirm("Remove all finished links from the list?")) return;
  try {
    const r = await api("/api/clear-finished", "POST");
    toast("Cleared " + (r?.cleared ?? 0) + " finished link(s)", "ok");
    refreshData();
  } catch (e) { toast("Failed: " + e.message, "err"); }
});

/* ============================================================ settings */
const SET_FIELDS = ["download_dir", "max_parallel", "per_link_chunks",
  "speed_limit_kbps", "media_max_height", "media_playlist_max_entries",
  "retry_max", "impersonate", "proxy", "captcha_provider",
  "captcha_2captcha_key", "captcha_capmonster_key", "captcha_capsolver_key",
  "ua_rotation", "auto_start_after_grab", "clipboard_monitor_enabled",
  "terminal_enabled", "auto_extract_archives", "archive_max_files",
  "archive_max_expanded_gib", "archive_password_file",
  "captcha_ocr_enabled", "captcha_ocr_ignore_hardware", "toasts_enabled",
  "scheduler_enabled", "max_per_host", "categories_enabled", "hash_checksum_files",
  "proxy_use_system", "linkgrabber_online_check", "cnl_enabled", "cnl_port",
  "spider_max_pages", "spider_delay_s", "spider_respect_robots", "archive_7zip_path",
  "torrent_listen_port", "torrent_open_files", "tracker_list_url",
  "plugin_sandbox_enabled", "plugin_index_url", "plugin_auto_update", "marketplace_url",
  "marketplace_require_signature", "anti_standby", "when_done", "when_done_delay_s", "when_done_once",
  "hooks_enabled", "ui_theme", "ui_density", "ui_toolbar_labels", "allow_remote_http", "ui_language"];


/* ============================================================ W3-W5 flow control */
let FLOW = null;          // last /api/flow answer
let CATS = [];            // category rows being edited
const DAY_NAMES = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

async function loadFlow() {
  try { FLOW = await api("/api/flow"); } catch { return; }
  renderFlowChrome();
}

function renderFlowChrome() {
  if (!FLOW) return;
  const sel = $("#speed-profile");
  if (sel) {
    sel.innerHTML = FLOW.profiles.map(p => `<option ${p.name === FLOW.active_profile ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  }
  const lim = $("#limit-pill");
  if (lim) {
    const parts = [];
    parts.push(FLOW.effective_limit_bps ? "Limit " + humanBytes(FLOW.effective_limit_bps) + "/s" : "No limit");
    if (FLOW.schedule) parts.push(FLOW.schedule.action === "pause" ? "schedule: paused" : FLOW.schedule.action === "limit" ? "schedule: limited" : "schedule: on");
    if (FLOW.data_cap?.exceeded) parts.push("data cap reached");
    lim.textContent = parts.join(", ");
  }
  const qsel = $("#url-queue");
  if (qsel) {
    const cur = qsel.value;
    qsel.innerHTML = FLOW.queues.map(q => `<option ${q.name === cur ? "selected" : ""}>${esc(q.name)}</option>`).join("");
  }
  const tree = $("#tree-queues");
  if (tree) {
    const extra = FLOW.queues.filter(q => q.name !== "Main");
    tree.innerHTML = extra.map(q => `<button data-tab="all" data-filter="queue:${esc(q.name)}" class="tab sub${S.filter === "queue:" + q.name ? " active" : ""}"><i class="ti ti-queue"></i>${esc(q.name)}${q.paused ? " (paused)" : ""}<span class="count">${Array.from(S.links.values()).filter(l => l.extra?.queue === q.name && l.status !== "finished").length || ""}</span></button>`).join("");
  }
}

$("#speed-profile")?.addEventListener("change", async (ev) => {
  try { await api("/api/speed/profile", "POST", { name: ev.target.value }); toast("Speed profile: " + ev.target.value, "ok"); loadFlow(); }
  catch (e) { toast("Could not switch profile: " + e.message, "err"); }
});

function schedRow(w = {}) {
  const days = new Set(w.days ?? [0, 1, 2, 3, 4, 5, 6]);
  return `<tr>
    <td><input type="text" class="w-start" value="${esc(w.start || "09:00")}" placeholder="HH:MM"></td>
    <td><input type="text" class="w-end" value="${esc(w.end || "17:00")}" placeholder="HH:MM"></td>
    <td class="days">${DAY_NAMES.map((d, i) => `<label>${d}<input type="checkbox" class="w-day" data-day="${i}" ${days.has(i) ? "checked" : ""}></label>`).join("")}</td>
    <td><select class="w-action">${[["pause", "Pause downloads"], ["limit", "Limit speed"], ["allow", "Allow downloads"]].map(([v, t]) => `<option value="${v}" ${w.action === v ? "selected" : ""}>${t}</option>`).join("")}</select></td>
    <td><input type="number" class="w-speed" min="0" value="${Number(w.speed_kbps || 0)}"></td>
    <td><button class="sm danger" data-row-del>Delete</button></td></tr>`;
}

function profileRow(p = {}) {
  return `<tr data-upload="${Number(p.upload_limit || 0)}"><td><input type="text" class="p-name" value="${esc(p.name || "")}" placeholder="Profile name"></td>
    <td><input type="number" class="p-limit" min="0" value="${Math.round((p.download_limit || 0) / 1024)}"></td>
    <td><button class="sm danger" data-row-del>Delete</button></td></tr>`;
}

function hostRow(host = "", n = 1) {
  return `<tr><td><input type="text" class="h-host" value="${esc(host)}" placeholder="e.g. example.com"></td>
    <td><input type="number" class="h-limit" min="0" value="${n}"></td>
    <td><button class="sm danger" data-row-del>Delete</button></td></tr>`;
}

function catRow(c = {}) {
  return `<tr><td><input type="text" class="c-name" value="${esc(c.name || "")}"></td>
    <td><input type="text" class="c-exts" value="${esc((c.extensions || []).join(" "))}" placeholder=".mp4 .mkv"></td>
    <td><input type="text" class="c-folder" value="${esc(c.folder || "")}"></td>
    <td><button class="sm danger" data-row-del>Delete</button></td></tr>`;
}

function queueRow(q) {
  const main = q.name === "Main";
  return `<tr data-queue="${esc(q.name)}"><td>${esc(q.name)}${main ? " (default)" : ""}</td>
    <td>${main ? '<span class="hint">uses "Downloads at the same time"</span>' : `<input type="number" class="q-max" min="1" max="32" value="${q.max_active}">`}</td>
    <td><input type="checkbox" class="q-paused" ${q.paused ? "checked" : ""}></td>
    <td>${main ? "" : '<button class="sm danger" data-queue-del>Delete</button>'}</td></tr>`;
}

function loadFlowSettings(s) {
  $("#sched-body").innerHTML = (s.scheduler_windows || []).map(schedRow).join("");
  const profiles = (s.speed_profiles && s.speed_profiles.profiles) || (FLOW ? FLOW.profiles : []);
  $("#profiles-body").innerHTML = profiles.map(profileRow).join("");
  const cap = s.data_cap || {};
  $("#cap-enabled").checked = !!cap.enabled;
  $("#cap-limit").value = cap.limit_mb ?? 1024;
  $("#cap-period").value = cap.period || "day";
  $("#cap-action").value = cap.action || "stop";
  $("#cap-slow").value = cap.slow_kbps ?? 64;
  $("#hostlim-body").innerHTML = Object.entries(s.per_host_limits || {}).map(([h, n]) => hostRow(h, n)).join("");
  CATS = (FLOW && FLOW.categories) || [];
  $("#cats-body").innerHTML = CATS.map(catRow).join("");
  renderQueuesTable();
  const u = FLOW?.data_cap;
  $("#cap-usage").textContent = u ? `Used ${humanBytes(u.used)} of ${humanBytes(u.limit_bytes)} this ${u.period}.` : "";
  $("#sched-now").textContent = FLOW?.schedule ? `Now in a "${FLOW.schedule.action}" window (${FLOW.schedule.start}–${FLOW.schedule.end}).` : "";
}

function renderQueuesTable() {
  if (!FLOW) return;
  $("#queues-body").innerHTML = FLOW.queues.map(queueRow).join("");
}

function collectFlowSettings(body) {
  body.scheduler_windows = $$("#sched-body tr").map(tr => ({
    start: $(".w-start", tr).value.trim(), end: $(".w-end", tr).value.trim(),
    days: $$(".w-day", tr).filter(c => c.checked).map(c => +c.dataset.day),
    action: $(".w-action", tr).value, speed_kbps: +$(".w-speed", tr).value || 0,
  }));
  const profiles = $$("#profiles-body tr").map(tr => ({
    name: $(".p-name", tr).value.trim(), download_limit: (+$(".p-limit", tr).value || 0) * 1024,
    upload_limit: +tr.dataset.upload || 0,
  })).filter(p => p.name);
  const active = FLOW?.active_profile && profiles.some(p => p.name === FLOW.active_profile) ? FLOW.active_profile : (profiles[0]?.name || "Normal");
  body.speed_profiles = profiles.length ? { active, profiles } : {};
  body.data_cap = { enabled: $("#cap-enabled").checked, limit_mb: +$("#cap-limit").value || 0,
                    period: $("#cap-period").value, action: $("#cap-action").value, slow_kbps: +$("#cap-slow").value || 64 };
  body.per_host_limits = Object.fromEntries($$("#hostlim-body tr")
    .map(tr => [$(".h-host", tr).value.trim().toLowerCase(), +$(".h-limit", tr).value || 0]).filter(([h]) => h));
  body.categories = $$("#cats-body tr").map(tr => ({
    name: $(".c-name", tr).value.trim(),
    extensions: $(".c-exts", tr).value.split(/[\s,]+/).filter(Boolean),
    folder: $(".c-folder", tr).value.trim(),
  })).filter(c => c.name);
}

document.addEventListener("click", async (ev) => {
  const del = ev.target.closest("[data-row-del]");
  if (del) { del.closest("tr").remove(); return; }
  const qdel = ev.target.closest("[data-queue-del]");
  if (qdel) {
    const name = qdel.closest("tr").dataset.queue;
    if (!confirm(`Delete queue ${name}? Its downloads move to Main.`)) return;
    try { await api("/api/queues/" + encodeURIComponent(name), "DELETE"); await loadFlow(); renderQueuesTable(); }
    catch (e) { toast("Could not delete queue: " + e.message, "err"); }
  }
});
document.addEventListener("change", async (ev) => {
  const tr = ev.target.closest("#queues-body tr");
  if (!tr) return;
  const name = tr.dataset.queue, body = {};
  if (ev.target.classList.contains("q-max")) body.max_active = +ev.target.value || 1;
  if (ev.target.classList.contains("q-paused")) body.paused = ev.target.checked;
  try { await api("/api/queues/" + encodeURIComponent(name), "POST", body); await loadFlow(); }
  catch (e) { toast("Could not change queue: " + e.message, "err"); }
});
$("#btn-sched-add").addEventListener("click", () => $("#sched-body").insertAdjacentHTML("beforeend", schedRow({ action: "pause" })));
$("#btn-profile-add").addEventListener("click", () => $("#profiles-body").insertAdjacentHTML("beforeend", profileRow({})));
$("#btn-hostlim-add").addEventListener("click", () => $("#hostlim-body").insertAdjacentHTML("beforeend", hostRow("", 1)));
$("#btn-cat-add").addEventListener("click", () => $("#cats-body").insertAdjacentHTML("beforeend", catRow({})));
$("#btn-cat-reset").addEventListener("click", async () => {
  if (!confirm("Restore the built-in categories? Your category edits are discarded when you save.")) return;
  $("#cats-body").innerHTML = "";
  CATS = [];
  toast("Built-in categories return after you save.", "");
});
$("#btn-queue-add").addEventListener("click", async () => {
  const name = $("#queue-new-name").value.trim();
  if (!name) return toast("Give the queue a name", "err");
  try {
    await api("/api/queues", "POST", { name, max_active: +$("#queue-new-max").value || 1 });
    $("#queue-new-name").value = "";
    await loadFlow(); renderQueuesTable();
  } catch (e) { toast("Could not create queue: " + e.message, "err"); }
});

/* ============================================================ W7 proxies + logins */
function proxyRuleRow(pattern = "", target = "direct") {
  return `<tr><td><input type="text" class="r-pattern" value="${esc(pattern)}" placeholder="*.example.com"></td>
    <td><input type="text" class="r-target" value="${esc(target)}"></td>
    <td><button class="sm danger" data-row-del>Delete</button></td></tr>`;
}
async function loadNetSettings(s) {
  $("#proxy-list").value = (s.proxies || []).join("\n");
  $("#proxy-bypass").value = (s.proxy_bypass || []).join("\n");
  $("#proxyrule-body").innerHTML = Object.entries(s.proxy_rules || {}).map(([p, t]) => proxyRuleRow(p, t)).join("");
  try {
    const info = await api("/api/proxies");
    const parts = [];
    if (info.proxies.length) parts.push("In use: " + info.proxies.join(", "));
    if (info.failed.length) parts.push("Not reachable lately: " + info.failed.join(", "));
    (info.errors || []).forEach(e => parts.push("Ignored: " + e));
    $("#proxy-status").textContent = parts.join(". ");
  } catch {}
}
function collectNetSettings(body) {
  const lines = id => $(id).value.split("\n").map(x => x.trim()).filter(Boolean);
  body.proxies = lines("#proxy-list");
  body.proxy_bypass = lines("#proxy-bypass");
  body.proxy_rules = Object.fromEntries($$("#proxyrule-body tr")
    .map(tr => [$(".r-pattern", tr).value.trim(), $(".r-target", tr).value.trim() || "direct"]).filter(([p]) => p));
}
$("#btn-proxyrule-add").addEventListener("click", () => $("#proxyrule-body").insertAdjacentHTML("beforeend", proxyRuleRow()));
$("#btn-proxy-test").addEventListener("click", async () => {
  const proxy = $("#proxy-test-addr").value.trim() || $("#proxy-list").value.split("\n")[0].trim();
  if (!proxy) return toast("Enter a proxy to test", "err");
  $("#proxy-test-result").textContent = "Testing…";
  try {
    const r = await api("/api/proxies/test", "POST", { proxy });
    $("#proxy-test-result").textContent = r.ok ? `Works (${r.ms} ms)` : "Failed: " + (r.error || "HTTP " + r.status);
  } catch (e) { $("#proxy-test-result").textContent = "Failed: " + e.message; }
});
async function renderLogins() {
  const el = $("#logins-list");
  if (!el) return;
  try {
    const r = await api("/api/site-logins");
    el.innerHTML = r.logins.length ? r.logins.map(l => `<div class="krow">
        <code>${esc(l.pattern)}</code><span class="badge">${esc(l.scheme)}</span>
        <span class="grow">${esc(l.username)}${l.for_proxy ? " (proxy)" : ""}</span>
        <button class="sm danger" data-login-del="${esc(l.pattern)}" data-proxy="${l.for_proxy ? 1 : 0}">Remove</button></div>`).join("")
      : '<div class="empty">No server or proxy logins.</div>';
  } catch {}
}
$("#btn-login-add").addEventListener("click", async () => {
  const body = { pattern: $("#login-pattern").value.trim(), username: $("#login-user").value.trim(),
                 password: $("#login-pass").value, scheme: $("#login-scheme").value, for_proxy: $("#login-proxy").checked };
  if (!body.pattern || !body.username) return toast("Enter a site and a user name", "err");
  try {
    await api("/api/site-logins", "POST", body);
    $("#login-pass").value = "";
    toast("Login saved", "ok");
    renderLogins();
  } catch (e) { toast("Could not save login: " + e.message, "err"); }
});
document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-login-del]");
  if (!b) return;
  try {
    await api(`/api/site-logins?pattern=${encodeURIComponent(b.dataset.loginDel)}&proxy=${b.dataset.proxy}`, "DELETE");
    renderLogins();
  } catch (e) { toast("Could not remove login: " + e.message, "err"); }
});

/* ============================================================ W10 virus scan */
async function loadScanSettings(s) {
  const av = s.avscan || {};
  $("#av-enabled").checked = !!av.enabled;
  $("#av-scanner").value = av.scanner || "defender";
  $("#av-command").value = av.custom_command || "";
  $("#av-codes").value = (av.infected_codes || [2]).join(" ");
  $("#av-timeout").value = av.timeout_s || 300;
  $("#av-quarantine").checked = !!av.quarantine;
  try {
    const r = await api("/api/avscan");
    $("#av-defender").textContent = r.defender ? "Windows Defender was found on this PC." : "Windows Defender's command line was not found on this PC.";
  } catch {}
}
$("#btn-av-test").addEventListener("click", async () => {
  $("#av-test-result").textContent = "Scanning a harmless test file…";
  try {
    const r = await api("/api/avscan/test", "POST", {});
    $("#av-test-result").textContent = r.status === "clean" ? "The scanner works (test file is clean)."
      : `Scanner answered "${r.status}"${r.code != null ? " (exit code " + r.code + ")" : ""}. ${r.detail || ""}`;
  } catch (e) { $("#av-test-result").textContent = e.message + " (save settings first)"; }
});

/* ============================================================ W11 BitTorrent */
async function loadTorrentSettings(s) {
  const t = s.torrent || {};
  window.__torrentRaw = t; window.__portmapRaw = s.portmap || {};
  $("#tor-dht").checked = t.dht !== false; $("#tor-pex").checked = t.pex !== false; $("#tor-lsd").checked = t.lsd !== false;
  $("#tor-seq").checked = !!t.sequential; $("#tor-ratio").value = t.ratio_limit || 0; $("#tor-minutes").value = t.seed_minutes || 0;
  $("#pm-enabled").checked = !!(s.portmap || {}).enabled;
  const r = s.reconnect || {};
  $("#rc-enabled").checked = !!r.enabled; $("#rc-command").value = r.command || ""; $("#rc-ipurl").value = r.ip_check_url || "";
  $("#rc-minwait").value = r.min_wait_seconds ?? 300; $("#rc-cooldown").value = r.cooldown_seconds ?? 600; $("#rc-attempts").value = r.max_attempts ?? 3;
  renderTrackers();
}
async function renderTrackers() {
  try {
    const r = await api("/api/trackers");
    $("#trackers-body").innerHTML = r.trackers.map(t => `<tr><td><code>${esc(t.url)}</code></td>
      <td>${t.successes || t.failures ? (t.healthy ? "yes" : "no") : "not tried"}</td><td>${t.seeds || 0}</td><td>${t.peers || 0}</td>
      <td class="hint">${esc(t.last_error || "")}</td><td><button class="sm danger" data-tracker-del="${esc(t.url)}">Remove</button></td></tr>`).join("");
  } catch {}
}
$("#btn-tracker-add").addEventListener("click", async () => {
  const url = $("#tracker-add").value.trim(); if (!url) return;
  try { const r = await api("/api/trackers", "POST", { url }); toast(r.added ? "Tracker added" : "That tracker is already in the list or not valid", r.added ? "ok" : "err"); $("#tracker-add").value = ""; renderTrackers(); }
  catch (e) { toast(e.message, "err"); }
});
$("#btn-tracker-update").addEventListener("click", async () => {
  try { const r = await api("/api/trackers/update", "POST", {}); toast(`Added ${r.added} tracker(s)`, "ok"); renderTrackers(); }
  catch (e) { toast("Update failed: " + e.message, "err"); }
});
$("#btn-pm-test").addEventListener("click", async () => {
  $("#pm-result").textContent = "Asking the router…";
  try { const r = await api("/api/portmap/test", "POST", {});
    $("#pm-result").textContent = r.ok ? `Port ${r.port} is open (${r.method === "natpmp" ? "NAT-PMP" : r.method.toUpperCase()}).` : "Not mapped: " + (r.error || "no answer"); }
  catch (e) { $("#pm-result").textContent = e.message; }
});
$("#btn-rc-now").addEventListener("click", async () => {
  if (!confirm("Run the router reset command now? Downloads may pause while the connection drops.")) return;
  $("#rc-result").textContent = "Reconnecting…";
  try { const r = await api("/api/reconnect", "POST", {});
    $("#rc-result").textContent = r.ok ? `New IP address ${r.new_ip}.` : "Did not work: " + r.error; }
  catch (e) { $("#rc-result").textContent = e.message + " (save settings first)"; }
});
document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-tracker-del]");
  if (!b) return;
  try { await api("/api/trackers?url=" + encodeURIComponent(b.dataset.trackerDel), "DELETE"); renderTrackers(); }
  catch (e) { toast(e.message, "err"); }
});
let filesFor = null;
async function openTorrentFiles(lid) {
  try {
    const r = await api(`/api/torrents/${lid}/files`);
    if (!r.files.length) return toast("The file list is not known yet. Try again when the torrent shows its size.", "");
    filesFor = lid;
    $("#files-dialog-list").innerHTML = r.files.map(f => `<label class="krow check"><input type="checkbox" data-file="${f.index}" ${f.selected ? "checked" : ""}>
      <span class="grow">${esc(f.path)}</span><span class="hint">${humanBytes(f.size)}</span></label>`).join("");
    $("#files-dialog").showModal();
  } catch (e) { toast(e.message, "err"); }
}
$("#files-close").addEventListener("click", () => $("#files-dialog").close());
$("#files-cancel").addEventListener("click", () => $("#files-dialog").close());
$("#files-save").addEventListener("click", async () => {
  const selected = $$("#files-dialog-list [data-file]").filter(c => c.checked).map(c => +c.dataset.file);
  if (!selected.length) return toast("Pick at least one file", "err");
  try { await api(`/api/torrents/${filesFor}/files`, "POST", { selected }); $("#files-dialog").close(); toast("File selection saved", "ok"); }
  catch (e) { toast(e.message, "err"); }
});

/* ============================================================ W12 plugins */
async function renderPluginsPage() {
  renderPermissions();
  renderMarketplace();
}
async function renderPermissions() {
  try {
    const r = await api("/api/plugins/permissions");
    $("#perm-body").innerHTML = r.plugins.length ? r.plugins.map(p => {
      const ask = [...p.requested.hosts, p.requested.cookies ? "cookies" : "", p.requested.files ? "files" : ""].filter(Boolean).join(", ") || "nothing";
      const has = [...p.granted.hosts, p.granted.cookies ? "cookies" : "", p.granted.files ? "files" : ""].filter(Boolean).join(", ") || "nothing";
      return `<tr><td>${esc(p.id)} <span class="hint">${esc(p.origin)}</span></td><td>${esc(ask)}</td><td>${esc(has)}</td>
        <td>${p.missing.length ? `<button class="sm primary" data-perm-grant="${esc(p.id)}">Allow</button>` : ""}
        <button class="sm" data-perm-revoke="${esc(p.id)}">Revoke</button></td></tr>`;
    }).join("") : `<tr><td colspan="4" class="hint">No plugins or templates of your own.</td></tr>`;
  } catch {}
}
async function renderMarketplace() {
  const el = $("#mk-list");
  try {
    const q = encodeURIComponent($("#mk-search").value.trim());
    const r = await api(`/api/marketplace?q=${q}&sort=${$("#mk-sort").value}`);
    el.innerHTML = r.listings.length ? r.listings.map(l => `<div class="krow">
      <span class="grow"><b>${esc(l.name)}</b> ${esc(l.version)} <span class="hint">by ${esc(l.author || "unknown")}</span>
      <div class="hint">${esc(l.description)}</div>
      <div class="hint">Needs: ${esc(l.permissions.hosts.join(", ") || "no sites")}${l.permissions.cookies ? ", cookies" : ""}</div></span>
      <span class="hint">${"★".repeat(Math.round(l.rating))}${"☆".repeat(5 - Math.round(l.rating))} (${l.ratings_count})</span>
      ${l.signed ? '<span class="badge include">signed</span>' : '<span class="badge exclude">unsigned</span>'}
      ${l.installed && !l.update ? `<span class="badge">installed ${esc(l.installed)}</span>`
        : `<button class="sm primary" data-mk-install="${esc(l.id)}">${l.update ? "Update" : "Install"}</button>`}
    </div>`).join("") : '<div class="empty">No plugins match.</div>';
  } catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
$("#btn-mk-search").addEventListener("click", renderMarketplace);
$("#btn-tpl-validate").addEventListener("click", async () => {
  try {
    const r = await api("/api/plugins/validate", "POST", { template: $("#tpl-text").value });
    $("#tpl-result").textContent = r.ok ? "The template is valid." : "Problems: " + r.errors.join("; ");
  } catch (e) { $("#tpl-result").textContent = e.message; }
});
$("#btn-tpl-update").addEventListener("click", async () => {
  $("#tpl-update-result").textContent = "Checking…";
  try {
    const r = await api("/api/plugins/update", "POST", {});
    const failed = Object.keys(r.failed || {});
    $("#tpl-update-result").textContent = (r.updated.length ? "Updated: " + r.updated.join(", ") : "Everything is up to date.")
      + (failed.length ? " Failed: " + failed.join(", ") : "");
    loadPluginHealth();
  } catch (e) { $("#tpl-update-result").textContent = e.message; }
});
document.addEventListener("click", async (ev) => {
  const g = ev.target.closest("[data-perm-grant]"), rv = ev.target.closest("[data-perm-revoke]"), mi = ev.target.closest("[data-mk-install]");
  try {
    if (g) { await api(`/api/plugins/${encodeURIComponent(g.dataset.permGrant)}/permissions`, "POST", {}); renderPermissions(); }
    else if (rv) { await api(`/api/plugins/${encodeURIComponent(rv.dataset.permRevoke)}/permissions`, "DELETE"); renderPermissions(); }
    else if (mi) {
      if (!confirm("Install this plugin and allow the access it lists?")) return;
      await api("/api/marketplace/install", "POST", { id: mi.dataset.mkInstall });
      toast("Plugin installed", "ok"); renderPluginsPage(); loadPluginHealth();
    }
  } catch (e) { toast(e.message, "err"); }
});

/* ============================================================ M13 automation */
const AUTO_EVENTS = ["finished", "failed", "all_done", "captcha", "added", "virus"];
const EVENT_TEXT = { finished: "a download finishes", failed: "a download fails", all_done: "all downloads are done",
  captcha: "a captcha waits", added: "links are added", virus: "a virus is found" };
function hookRow(h = {}) {
  return `<tr><td><select class="h-event">${AUTO_EVENTS.map(e => `<option value="${e}" ${h.event === e ? "selected" : ""}>${EVENT_TEXT[e]}</option>`).join("")}</select></td>
    <td><input type="text" class="h-cmd" value="${esc(h.command || "")}" placeholder='"C:\\Tools\\notify.exe" --title done'></td>
    <td><input type="checkbox" class="h-on" ${h.enabled !== false ? "checked" : ""}></td>
    <td><button class="sm danger" data-row-del>Delete</button></td></tr>`;
}
function webhookRow(w = {}) {
  return `<tr><td><input type="text" class="w-url" value="${esc(w.url || "")}" placeholder="https://…"></td>
    <td><input type="text" class="w-events" value="${esc((w.events || []).join(" "))}" placeholder="finished failed"></td>
    <td><input type="password" class="w-secret" value="${esc(w.secret || "")}"></td>
    <td><button class="sm danger" data-row-del>Delete</button></td></tr>`;
}
function watchRow(f = {}) {
  return `<tr><td><input type="text" class="f-path" value="${esc(f.path || "")}" placeholder="C:\\Users\\me\\Downloads\\watch"></td>
    <td><select class="f-mode"><option value="review" ${f.mode !== "queue" ? "selected" : ""}>wait in the LinkGrabber</option><option value="queue" ${f.mode === "queue" ? "selected" : ""}>download right away</option></select></td>
    <td><input type="checkbox" class="f-on" ${f.enabled !== false ? "checked" : ""}></td>
    <td><button class="sm danger" data-row-del>Delete</button></td></tr>`;
}
function loadAutomationSettings(s) {
  $("#hooks-body").innerHTML = (s.event_hooks || []).map(hookRow).join("");
  $("#webhooks-body").innerHTML = (s.webhooks || []).map(webhookRow).join("");
  $("#watch-body").innerHTML = (s.folder_watch || []).map(watchRow).join("");
  $("#when-done-pick").value = s.when_done || "nothing";
}
function collectAutomationSettings(body) {
  body.event_hooks = $$("#hooks-body tr").map(tr => ({ event: $(".h-event", tr).value, command: $(".h-cmd", tr).value.trim(),
    enabled: $(".h-on", tr).checked })).filter(h => h.command);
  body.webhooks = $$("#webhooks-body tr").map(tr => ({ url: $(".w-url", tr).value.trim(),
    events: $(".w-events", tr).value.split(/[\s,]+/).filter(Boolean), secret: $(".w-secret", tr).value })).filter(w => w.url);
  body.folder_watch = $$("#watch-body tr").map(tr => ({ path: $(".f-path", tr).value.trim(), mode: $(".f-mode", tr).value,
    enabled: $(".f-on", tr).checked })).filter(f => f.path);
}
$("#btn-hook-add").addEventListener("click", () => $("#hooks-body").insertAdjacentHTML("beforeend", hookRow()));
$("#btn-webhook-add").addEventListener("click", () => $("#webhooks-body").insertAdjacentHTML("beforeend", webhookRow()));
$("#btn-watch-add").addEventListener("click", () => $("#watch-body").insertAdjacentHTML("beforeend", watchRow()));
$("#btn-auto-test").addEventListener("click", async () => {
  try { await api("/api/automation/test", "POST", { event: "finished" }); $("#auto-test-result").textContent = "Sent. Check the result below in a few seconds."; setTimeout(showAutomationHistory, 3000); }
  catch (e) { $("#auto-test-result").textContent = e.message + " (save settings first)"; }
});
async function showAutomationHistory() {
  try {
    const r = await api("/api/automation");
    const h = r.history[0];
    if (h) $("#auto-test-result").textContent = h.kind === "webhook" ? `Webhook ${h.url}: ${h.code || h.error}` : `Program exited with ${h.code}`;
  } catch {}
}
function showWhenDone(st) {
  const b = $("#whendone-banner");
  if (!b) return;
  if (st && st.pending) {
    const what = { exit: "close OpenGrab", sleep: "put the PC to sleep", hibernate: "hibernate the PC", shutdown: "shut down the PC" }[st.pending];
    $("#whendone-text").textContent = `All downloads are done. OpenGrab will ${what} in ${st.seconds_left ?? "a few"} seconds.`;
    b.classList.remove("hidden");
  } else b.classList.add("hidden");
  if (st && st.action) $("#when-done-pick").value = st.action;
}
$("#btn-whendone-cancel").addEventListener("click", async () => {
  try { await api("/api/automation/cancel", "POST", {}); await api("/api/automation/when-done", "POST", { action: "nothing" }); showWhenDone(null); toast("Cancelled", "ok"); }
  catch (e) { toast(e.message, "err"); }
});
$("#when-done-pick").addEventListener("change", async (ev) => {
  try { await api("/api/automation/when-done", "POST", { action: ev.target.value }); toast("When done: " + ev.target.selectedOptions[0].textContent, "ok"); }
  catch (e) { toast(e.message, "err"); }
});
setInterval(async () => {
  if ($("#whendone-banner")?.classList.contains("hidden")) return;
  try { showWhenDone((await api("/api/automation")).when_done); } catch {}
}, 1000);

/* ============================================================ M14a appearance */
const systemDark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
let UI = { ui_theme: "classic", ui_accent: "", ui_density: "normal", ui_hidden_columns: [], ui_toolbar_labels: true };
function applyUi(prefs) {
  UI = Object.assign(UI, prefs || {});
  const dark = UI.ui_theme === "classic-dark" || (UI.ui_theme === "system" && systemDark && systemDark.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  if (UI.ui_accent) document.documentElement.style.setProperty("--accent", UI.ui_accent);
  else document.documentElement.style.removeProperty("--accent");
  const b = document.body;
  b.classList.remove("density-compact", "density-comfortable");
  if (UI.ui_density !== "normal") b.classList.add("density-" + UI.ui_density);
  b.classList.toggle("no-tb-labels", !UI.ui_toolbar_labels);
  for (const c of ["size", "status", "eta", "speed", "progress"]) b.classList.toggle("hide-col-" + c, (UI.ui_hidden_columns || []).includes(c));
  try { localStorage.setItem("og_ui", JSON.stringify(UI)); } catch {}
}
try { applyUi(JSON.parse(localStorage.getItem("og_ui") || "{}")); } catch { applyUi({}); }
systemDark?.addEventListener?.("change", () => applyUi({}));
function syncLanguage(want) {
  // the page is translated at load; a changed language needs one reload
  let have = "auto";
  try { have = localStorage.getItem("og_lang") || "auto"; } catch {}
  if (want && want !== have) {
    try { localStorage.setItem("og_lang", want); } catch {}
    location.reload();
  }
}
function maybeWelcome(s) {
  if (s.onboarding_done || $("#welcome-dialog").open) return;
  $("#wel-language").value = s.ui_language || "auto";
  $("#wel-theme").value = s.ui_theme || "classic";
  $("#wel-folder").value = s.download_dir || "";
  $("#wel-clipboard").checked = !!s.clipboard_monitor_enabled;
  $("#welcome-dialog").showModal();
}
async function finishWelcome(save) {
  const body = { onboarding_done: true };
  if (save) Object.assign(body, {
    ui_language: $("#wel-language").value, ui_theme: $("#wel-theme").value,
    download_dir: $("#wel-folder").value.trim(), clipboard_monitor_enabled: $("#wel-clipboard").checked,
  });
  try {
    await api("/api/settings", "POST", body);
    if (save && $("#wel-ocr").checked) {
      await api("/api/settings", "POST", { captcha_ocr_enabled: true });
      await api("/api/addons/ocr/install", "POST", {});
      toast("Downloading the local OCR solver in the background", "ok");
    }
  } catch (e) { toast("Could not save: " + e.message, "err"); }
  $("#welcome-dialog").close();
  if (save) { applyUi({ ui_theme: body.ui_theme }); setFolderPill(body.download_dir); syncLanguage(body.ui_language); }
}
$("#wel-done").addEventListener("click", () => finishWelcome(true));
$("#wel-skip").addEventListener("click", () => finishWelcome(false));
$("#welcome-dialog").addEventListener("cancel", () => finishWelcome(false));
function loadAppearance(s) {
  syncLanguage(s.ui_language || "auto");
  maybeWelcome(s);
  $("#set-ui_accent").value = s.ui_accent || "#1c5db8";
  $$("fieldset.cols [data-col]").forEach(c => c.checked = !(s.ui_hidden_columns || []).includes(c.dataset.col));
  $("#adv-proxies").value = (s.trusted_proxies || []).join(", ");
  applyUi({ ui_theme: s.ui_theme, ui_accent: s.ui_accent, ui_density: s.ui_density,
            ui_hidden_columns: s.ui_hidden_columns, ui_toolbar_labels: s.ui_toolbar_labels });
}
function collectAppearance(body) {
  body.ui_accent = UI.ui_accent || "";
  body.ui_hidden_columns = $$("fieldset.cols [data-col]").filter(c => !c.checked).map(c => c.dataset.col);
  body.trusted_proxies = $("#adv-proxies").value.split(",").map(x => x.trim()).filter(Boolean);
}
// preview appearance changes immediately
for (const id of ["#set-ui_theme", "#set-ui_density", "#set-ui_toolbar_labels"]) {
  $(id).addEventListener("change", () => applyUi({ ui_theme: $("#set-ui_theme").value, ui_density: $("#set-ui_density").value,
                                                    ui_toolbar_labels: $("#set-ui_toolbar_labels").checked }));
}
$("#set-ui_accent").addEventListener("input", (ev) => applyUi({ ui_accent: ev.target.value }));
$("#btn-accent-reset").addEventListener("click", () => { applyUi({ ui_accent: "" }); $("#set-ui_accent").value = "#1c5db8"; });
$$("fieldset.cols [data-col]").forEach(c => c.addEventListener("change", () =>
  applyUi({ ui_hidden_columns: $$("fieldset.cols [data-col]").filter(x => !x.checked).map(x => x.dataset.col) })));
async function loadAdvanced() {
  try {
    const s = await api("/api/settings");
    $("#adv-json").value = JSON.stringify(s, null, 2);
    $("#adv-result").textContent = "";
  } catch (e) { $("#adv-result").textContent = e.message; }
}
$("#btn-adv-load").addEventListener("click", loadAdvanced);
document.querySelector(".advanced-editor")?.addEventListener("toggle", (ev) => { if (ev.target.open) loadAdvanced(); });
$("#btn-adv-save").addEventListener("click", async () => {
  let body;
  try { body = JSON.parse($("#adv-json").value); } catch (e) { $("#adv-result").textContent = "Not valid JSON: " + e.message; return; }
  try {
    const r = await api("/api/settings", "POST", body);
    const parts = [`Saved ${r.applied.length} setting(s).`];
    if (r.ignored.length) parts.push("Not changeable here: " + r.ignored.join(", ") + ".");
    (r.warnings || []).forEach(w => parts.push(w));
    $("#adv-result").textContent = parts.join(" ");
    loadSettings();
  } catch (e) { $("#adv-result").textContent = "Not saved: " + e.message; }
});

/* ============================================================ add-ons */
let addonPoll = null;
async function loadAddons() {
  try {
    const r = await api("/api/addons");
    for (const a of r.addons || []) renderAddon(a, r.hardware || {});
  } catch {
    for (const id of ["#addon-ocr-status", "#addon-torrent-status"]) if ($(id)) $(id).textContent = "Add-on status unavailable.";
  }
}
function renderAddon(a, hw) {
  const st = $(`#addon-${a.name}-status`);
  if (!st) return;
  {
    const job = a.job;
    let text;
    if (job && job.running) {
      const pct = job.total ? Math.round(100 * job.done / job.total) : 0;
      text = `Installing: ${job.step || ""} (${pct}%)`;
    } else if (a.installed) {
      text = `Installed, ${humanBytes(a.disk_bytes)} on disk.`;
    } else {
      text = `Not installed. Download size ${humanBytes(a.download_bytes)}.`;
      if (job && job.error) text += " Last attempt failed: " + job.error;
    }
    if (a.name === "ocr" && hw.weak) text += " This PC counts as weak hardware (" + (hw.reasons || []).join(", ") + ").";
    st.textContent = text;
    $(`#btn-addon-${a.name}-install`).hidden = a.installed;
    $(`#btn-addon-${a.name}-install`).disabled = !!(job && job.running);
    $(`#btn-addon-${a.name}-remove`).hidden = !a.installed;
    if (job && job.running) { clearTimeout(addonPoll); addonPoll = setTimeout(loadAddons, 1000); }
  }
}
for (const name of ["ocr", "torrent"]) {
  $(`#btn-addon-${name}-install`)?.addEventListener("click", async () => {
    try { await api(`/api/addons/${name}/install`, "POST", {}); loadAddons(); }
    catch (e) { toast("Install failed: " + e.message, "err"); }
  });
  $(`#btn-addon-${name}-remove`)?.addEventListener("click", async () => {
    if (!confirm("Remove this add-on?")) return;
    try { await api(`/api/addons/${name}/remove`, "POST", {}); loadAddons(); }
    catch (e) { toast("Remove failed: " + e.message, "err"); }
  });
}

async function loadSettings() {
  loadDevices();
  loadAddons();
  loadIntegration();
  loadUpdate(false);
  try {
    const s = await api("/api/settings");
    for (const f of SET_FIELDS) {
      const el = $("#set-" + f);
      if (!el) continue;
      if (el.type === "checkbox") el.checked = !!s[f];
      else el.value = s[f] ?? "";
    }
    $("#set-confirm_delete").checked = !!s.confirm_delete;
    setFolderPill(s.download_dir);
    await loadFlow();
    loadFlowSettings(s);
    loadNetSettings(s);
    loadScanSettings(s);
    loadTorrentSettings(s);
    $("#mk-keys").value = (s.marketplace_keys || []).join("\n");
    loadAutomationSettings(s);
    loadAppearance(s);
    $("#clipboard-state").textContent = s.clipboard_monitor_enabled
      ? "Clipboard monitoring is on; new links wait here for your approval."
      : "Clipboard monitoring is off.";
    $("#token-display").textContent = S.token || "(local only)";
    $("#lan-url").textContent = s.api_host === "127.0.0.1"
      ? "http://<your-ip>:" + s.api_port + BASE_PATH + "/#token="  + S.token
      : location.origin + BASE_PATH + "/";
  } catch { /* banner shown on 401 */ }
}

$("#btn-set-save").addEventListener("click", async () => {
  const body = {};
  for (const f of SET_FIELDS) {
    const el = $("#set-" + f);
    if (!el) continue;
    body[f] = el.type === "checkbox" ? el.checked
      : ["max_parallel", "per_link_chunks", "speed_limit_kbps", "retry_max",
        "archive_max_files", "archive_max_expanded_gib", "media_max_height",
        "media_playlist_max_entries", "max_per_host", "cnl_port", "spider_max_pages",
        "spider_delay_s", "torrent_listen_port", "when_done_delay_s"].includes(f)
        ? +el.value : el.value;
  }
  body.confirm_delete = $("#set-confirm_delete").checked;
  collectFlowSettings(body);
  collectNetSettings(body);
  collectAutomationSettings(body);
  collectAppearance(body);
  body.marketplace_keys = $("#mk-keys").value.split("\n").map(x => x.trim()).filter(Boolean);
  const tor = window.__torrentRaw || {};
  body.torrent = Object.assign({}, tor, {
    dht: $("#tor-dht").checked, pex: $("#tor-pex").checked, lsd: $("#tor-lsd").checked,
    sequential: $("#tor-seq").checked, ratio_limit: +$("#tor-ratio").value || 0, seed_minutes: +$("#tor-minutes").value || 0,
  });
  body.portmap = Object.assign({}, window.__portmapRaw || {}, { enabled: $("#pm-enabled").checked });
  body.reconnect = {
    enabled: $("#rc-enabled").checked, command: $("#rc-command").value.trim(),
    ip_check_url: $("#rc-ipurl").value.trim(), min_wait_seconds: +$("#rc-minwait").value || 0,
    cooldown_seconds: +$("#rc-cooldown").value || 0, max_attempts: +$("#rc-attempts").value || 1,
  };
  body.avscan = {
    enabled: $("#av-enabled").checked, scanner: $("#av-scanner").value,
    custom_command: $("#av-command").value.trim(),
    infected_codes: $("#av-codes").value.split(/[\s,]+/).filter(Boolean).map(Number).filter(n => !isNaN(n)),
    timeout_s: +$("#av-timeout").value || 300, quarantine: $("#av-quarantine").checked,
  };
  try {
    const res = await api("/api/settings", "POST", body);
    (res.warnings || []).forEach(w => toast("Not applied: " + w, "err"));
    await loadFlow();
    $("#clipboard-state").textContent = body.clipboard_monitor_enabled
      ? "Clipboard monitoring is on; new links wait here for your approval."
      : "Clipboard monitoring is off.";
    toast("Settings saved", "ok");
    syncLanguage(body.ui_language);
  } catch (e) { toast("Save failed: " + e.message, "err"); }
});

$("#btn-set-reload-plugins").addEventListener("click", async () => {
  try {
    const r = await api("/api/plugins/scan", "POST");
    toast("Plugin scan: " + (r?.scanned ?? 0) + " plugin(s)", "ok");
    loadPluginHealth();
  } catch (e) { toast("Scan failed: " + e.message, "err"); }
});

$("#btn-diagnostics-export").addEventListener("click", async () => {
  try {
    const snapshot = await api("/api/diagnostics");
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = "opengrab-diagnostics.json";
    link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (e) { toast("Could not create diagnostics: " + e.message, "err"); }
});

function setFolderPill(dir) {
  const el = $("#folder-pill");
  if (el && dir) { el.textContent = "Saving to " + dir; el.title = dir; }
}

/* ============================================================ paired devices */
function fmtTime(t) { return t ? new Date(t * 1000).toLocaleString() : "never"; }

async function loadDevices() {
  const body = $("#devices-body");
  if (!body) return;
  try {
    const r = await api("/api/devices");
    const list = (r && r.devices) || [];
    body.innerHTML = list.length
      ? list.map(d => `<tr><td>${esc(d.name)}</td><td>${esc(fmtTime(d.created))}</td><td>${esc(fmtTime(d.last_seen))}</td><td><button data-revoke="${esc(d.id)}">Revoke</button></td></tr>`).join("")
      : `<tr><td colspan="4" class="hint">No paired devices.</td></tr>`;
  } catch { body.innerHTML = `<tr><td colspan="4" class="hint">Paired devices are unavailable.</td></tr>`; }
}

$("#btn-pair-new").addEventListener("click", async () => {
  try {
    const r = await api("/api/pair/start", "POST", {});
    $("#pair-code").textContent = r.code;
    $("#pair-expiry").textContent = "expires in " + Math.max(1, Math.round(r.expires_in / 60)) + " min";
  } catch (e) { toast("Pairing failed: " + e.message, "err"); }
});

$("#devices-body").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-revoke]");
  if (!btn) return;
  try {
    await api("/api/devices/" + encodeURIComponent(btn.dataset.revoke), "DELETE");
    toast("Device revoked", "ok");
    loadDevices();
  } catch (e) { toast("Revoke failed: " + e.message, "err"); }
});

async function pollTerminal() {
  if (!terminalJobId) return;
  try {
    const result = await api(`/api/terminal/${terminalJobId}`);
    $("#terminal-output").textContent = result.output || "";
    if (result.running) {
      terminalPollTimer = setTimeout(pollTerminal, 500);
    } else {
      if (result.timed_out) $("#terminal-output").textContent += "\n[command timed out]";
      if (result.truncated) $("#terminal-output").textContent += "\n[earlier output omitted]";
      terminalJobId = null;
      $("#btn-terminal-stop").disabled = true;
    }
  } catch (e) {
    $("#terminal-output").textContent = e.message;
    terminalJobId = null;
    $("#btn-terminal-stop").disabled = true;
  }
}

$("#btn-terminal-run").addEventListener("click", async () => {
  const command = $("#terminal-command").value.trim();
  if (!command) return;
  try {
    if (!S.token) {
      const settings = await api("/api/settings");
      S.token = settings.api_token || "";
      if (S.token) localStorage.setItem("og_token", S.token);
    }
    const result = await api("/api/terminal", "POST", { command });
    terminalJobId = result.id;
    $("#terminal-output").textContent = "Running…";
    $("#btn-terminal-stop").disabled = false;
    pollTerminal();
  } catch (e) { toast("Terminal failed: " + e.message, "err"); }
});

$("#btn-terminal-stop").addEventListener("click", async () => {
  if (!terminalJobId) return;
  try {
    await api(`/api/terminal/${terminalJobId}/stop`, "POST", {});
    clearTimeout(terminalPollTimer);
    pollTerminal();
  } catch (e) { toast("Could not stop command: " + e.message, "err"); }
});

$("#btn-lg-paste").addEventListener("click", async () => {
  const urls = $("#lg-paste").value.split(/\s+/).filter(Boolean);
  if (!urls.length) return toast("Paste one or more links first", "err");
  try {
    const r = await api("/api/linkgrabber", "POST", { urls, source: "paste" });
    $("#lg-paste").value = "";
    toast(`${r.added} link(s) added for review`, "ok");
    renderLinkGrabber();
  } catch (e) { toast("Could not add links: " + e.message, "err"); }
});
$("#btn-sp-start").addEventListener("click", async () => {
  const url = $("#sp-url").value.trim();
  if (!url) return toast("Enter the address the spider starts from", "err");
  try {
    await api("/api/linkgrab/spider", "POST", {
      url, depth: +$("#sp-depth").value || 0, same_domain: $("#sp-same").checked,
      extensions: $("#sp-exts").value.trim(),
    });
    $("#sp-status").textContent = "Spider running…";
  } catch (e) { toast("Spider failed: " + e.message, "err"); }
});
$("#btn-lg-check").addEventListener("click", async () => {
  try { await api("/api/linkgrabber/check", "POST", {}); renderLinkGrabber(); } catch {}
});
$("#btn-lg-addonline").addEventListener("click", async () => {
  try {
    const r = await api("/api/linkgrabber/add-all", "POST", { only_online: true });
    toast(`Added ${r.added} online link(s)`, "ok");
    renderLinkGrabber(); refreshData();
  } catch (e) { toast("Could not add links: " + e.message, "err"); }
});
$("#btn-linkgrabber-clear").addEventListener("click", async () => {
  try {
    await api("/api/linkgrabber/clear", "POST", {});
    renderLinkGrabber();
  } catch (e) { toast("Could not clear candidates: " + e.message, "err"); }
});

$("#btn-notifications-read").addEventListener("click", async () => {
  try { await api("/api/notifications/read-all", "POST", {}); await renderNotifications(); }
  catch (e) { toast("Could not mark notifications read: " + e.message, "err"); }
});
$("#btn-notifications-clear").addEventListener("click", async () => {
  try { await api("/api/notifications/clear", "POST", {}); await renderNotifications(); }
  catch (e) { toast("Could not clear notifications: " + e.message, "err"); }
});
$("#btn-files-refresh").addEventListener("click", renderFiles);
$("#btn-files-up").addEventListener("click", () => {
  filesPath = filesPath.includes("/") ? filesPath.slice(0, filesPath.lastIndexOf("/")) : "";
  renderFiles();
});
$("#btn-files-mkdir").addEventListener("click", async () => {
  const name = prompt("Folder name"); if (!name) return;
  try { await api("/api/files", "POST", { action: "mkdir", path: childPath(name) }); await renderFiles(); }
  catch (e) { toast("Could not create folder: " + e.message, "err"); }
});
$("#file-upload").addEventListener("change", async ev => {
  const file = ev.target.files?.[0]; if (!file) return;
  try {
    const path = childPath(file.name);
    const headers = { "Content-Type": "application/octet-stream" };
    if (S.token) headers.Authorization = "Bearer " + S.token;
    const response = await fetch(withBasePath("/api/files/upload") + "?path=" + encodeURIComponent(path), { method: "PUT", headers, body: file });
    if (!response.ok) throw new Error((await response.json()).error || response.statusText);
    toast("Uploaded " + file.name, "ok"); await renderFiles();
  } catch (e) { toast("Upload failed: " + e.message, "err"); }
  ev.target.value = "";
});
$("#btn-pack-add").addEventListener("click", async () => {
  const body = {
    name: $("#pack-name").value.trim(),
    pattern: $("#pack-pattern").value.trim(),
    pattern_type: $("#pack-pattern-type").value,
    target_subdir: $("#pack-subdir").value.trim(),
    filename_template: $("#pack-template").value.trim(),
  };
  try {
    await api("/api/packagizer", "POST", body);
    $("#pack-name").value = ""; $("#pack-pattern").value = "";
    $("#pack-subdir").value = ""; $("#pack-template").value = "";
    await renderPackagizer(); toast("Packagizer rule added", "ok");
  } catch (e) { toast("Could not add rule: " + e.message, "err"); }
});
$("#btn-pack-preview").addEventListener("click", async () => {
  try {
    const plan = await api("/api/packagizer/preview", "POST", {
      url: $("#pack-preview-url").value.trim(),
      filename: $("#pack-preview-name").value.trim(),
    });
    $("#pack-preview-result").textContent = JSON.stringify(plan, null, 2);
  } catch (e) { toast("Preview failed: " + e.message, "err"); }
});

/* ============================================================ toasts */
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => { t.classList.add("leaving"); setTimeout(() => t.remove(), 350); }, 4200);
}

/* ============================================================ auth banner */
$("#auth-save").addEventListener("click", () => {
  S.token = $("#auth-token").value.trim();
  localStorage.setItem("og_token", S.token);
  $("#auth-banner").classList.add("hidden");
  refreshData();
  connectSSE();
});

async function loadPluginHealth() {
  try {
    const r = await api("/api/plugins");
    const h = r?.health || {};
    $("#plugin-health-body").innerHTML = Object.keys(h).sort().map(id =>
      `<tr><td>${esc(id)}</td><td>${h[id].ok}</td><td>${h[id].fail}</td><td>${esc(h[id].last_error || "")}</td></tr>`).join("");
  } catch (e) { toast("Plugin health failed: " + e.message, "err"); }
}

/* ============================================================ boot */
(function boot() {
  getToken();
  updatePills();
  renderGraph();
  refreshData();
  refreshUnread();
  renderCaptchas();
  loadFlow();
  api("/api/automation").then(r => showWhenDone(r.when_done)).catch(() => {});
  api("/api/settings").then(s => { setFolderPill(s.download_dir); loadAppearance(s); }).catch(() => {});
  loadPluginHealth();
  connectSSE();
  // PWA service worker (best effort; http contexts only register on https/localhost)
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register(withBasePath("/sw.js")).catch(() => {});
  }
  if (location.hash.includes("token=")) history.replaceState(null, "", location.pathname);
  setInterval(() => {
    renderGraph();
    if ($("#tab-linkgrabber")?.classList.contains("active")) renderLinkGrabber();
  }, 2000);
})();


/* ============================================================ M14 dashboard */
const DASH_TILES = {
  today: { title: "Today" },
  speed: { title: "Transfer rate, last 5 minutes", wide: true },
  active: { title: "Downloading now" },
  days: { title: "Downloaded per day, last 14 days", wide: true },
  sites: { title: "Busiest sites" },
};
let dashData = null, dashTimer = null;
function dashOrder() {
  const saved = (dashData?.tiles || []).filter(k => DASH_TILES[k]);
  return saved.concat(Object.keys(DASH_TILES).filter(k => !saved.includes(k)));
}
function dashTileBody(key, d) {
  if (key === "today") {
    return `<div class="dash-big">${humanBytes(d.today.bytes)}</div>
      <div class="dash-kv"><span>Finished</span><b>${d.today.finished}</b><span>Failed</span><b>${d.today.failed}</b>
      <span>Last 90 days</span><b>${humanBytes(d.total_bytes)}</b><span>Waiting</span><b>${(d.counts.waiting || 0) + (d.counts.queued || 0)}</b></div>`;
  }
  if (key === "speed") return `<canvas class="dash-meter" id="dash-speed" aria-label="Transfer rate over the last 5 minutes"></canvas>`;
  if (key === "active") {
    if (!d.active.length) return `<p class="dash-empty">Nothing is downloading.</p>`;
    return `<div class="dash-rows">${d.active.map(a => `<div class="dash-row"><span class="name" title="${esc(a.name)}">${esc(a.name)}</span>
      <span>${humanBytes(a.speed || 0)}/s</span><span class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round((a.progress || 0) * 100)}"><i style="width:${Math.round((a.progress || 0) * 100)}%"></i></span></div>`).join("")}</div>`;
  }
  if (key === "days") {
    const max = Math.max(1, ...d.days.map(x => x.bytes));
    return `<div class="dash-bars">${d.days.map((x, i) => `<div class="${i === d.days.length - 1 ? "today" : ""}" style="height:${Math.max(1, Math.round(x.bytes / max * 100))}%" title="${x.day}: ${humanBytes(x.bytes)}, ${x.finished} finished"></div>`).join("")}</div>
      <div class="dash-axis"><span>${d.days[0].day.slice(5)}</span><span>${humanBytes(max)} highest</span><span>today</span></div>`;
  }
  if (key === "sites") {
    if (!d.top_hosts.length) return `<p class="dash-empty">No downloads counted yet.</p>`;
    const max = d.top_hosts[0].bytes || 1;
    return `<div class="dash-rows">${d.top_hosts.map(h => `<div class="dash-row"><span class="name">${esc(h.host)}</span><span>${humanBytes(h.bytes)}</span>
      <span class="bar"><i style="width:${Math.round(h.bytes / max * 100)}%"></i></span></div>`).join("")}</div>`;
  }
  return "";
}
function renderDashboard() {
  const grid = $("#dash-grid");
  if (!grid || !dashData) return;
  const order = dashOrder();
  grid.innerHTML = order.map((k, i) => `<section class="dash-tile${DASH_TILES[k].wide ? " wide" : ""}" data-tile="${k}" draggable="true" aria-label="${esc(DASH_TILES[k].title)}">
    <div class="dash-title"><span class="grow">${esc(DASH_TILES[k].title)}</span>
      <button type="button" class="sm" data-move="-1" aria-label="Move earlier" title="Move earlier" ${i === 0 ? "disabled" : ""}>&#9664;</button>
      <button type="button" class="sm" data-move="1" aria-label="Move later" title="Move later" ${i === order.length - 1 ? "disabled" : ""}>&#9654;</button></div>
    <div class="dash-body">${dashTileBody(k, dashData)}</div></section>`).join("");
  drawDashSpeed();
}
function drawDashSpeed() {
  const cv = $("#dash-speed");
  if (!cv || !dashData) return;
  const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
  if (!w || !h) return;
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.strokeStyle = "rgba(120,170,230,.18)"; ctx.lineWidth = 1;
  for (let x = w; x > 0; x -= 16) { ctx.beginPath(); ctx.moveTo(x + .5, 0); ctx.lineTo(x + .5, h); ctx.stroke(); }
  for (let y = h; y > 0; y -= 20) { ctx.beginPath(); ctx.moveTo(0, y + .5); ctx.lineTo(w, y + .5); ctx.stroke(); }
  const pts = dashData.speed_history;
  if (pts.length < 2) return;
  const max = Math.max(64 * 1024, ...pts.map(p => p[1])) * 1.1, now = pts[pts.length - 1][0];
  const x = p => w - ((now - p[0]) / 300) * w, y = b => h - 1 - (b / max) * (h - 4);
  ctx.beginPath(); ctx.moveTo(x(pts[0]), h);
  for (const p of pts) ctx.lineTo(x(p), y(p[1]));
  ctx.lineTo(x(pts[pts.length - 1]), h); ctx.closePath();
  ctx.fillStyle = "rgba(38,160,51,.35)"; ctx.fill();
  ctx.beginPath(); ctx.moveTo(x(pts[0]), y(pts[0][1]));
  for (const p of pts) ctx.lineTo(x(p), y(p[1]));
  ctx.strokeStyle = "#5fe05f"; ctx.lineWidth = 1.4; ctx.stroke();
  ctx.fillStyle = "#9fd0ff"; ctx.font = "11px Segoe UI, sans-serif";
  ctx.fillText(`${humanBytes(max / 1.1)}/s`, 4, 12);
}
async function loadDashboard() {
  try { dashData = await api("/api/dashboard"); renderDashboard(); } catch { /* banner shown on 401 */ }
}
async function saveDashOrder(order) {
  dashData.tiles = order;
  renderDashboard();
  try { await api("/api/settings", "POST", { dashboard_tiles: order }); } catch (e) { toast("Could not save: " + e.message, "err"); }
}
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("#dash-grid [data-move]");
  if (!b) return;
  const order = dashOrder(), k = b.closest(".dash-tile").dataset.tile, i = order.indexOf(k), j = i + Number(b.dataset.move);
  if (j < 0 || j >= order.length) return;
  [order[i], order[j]] = [order[j], order[i]];
  saveDashOrder(order).then(() => $(`#dash-grid [data-tile="${k}"] [data-move="${b.dataset.move}"]`)?.focus());
});
let dashDrag = null;
document.addEventListener("dragstart", (ev) => {
  const t = ev.target.closest?.(".dash-tile");
  if (!t) return;
  dashDrag = t.dataset.tile; t.classList.add("dragging");
  ev.dataTransfer.effectAllowed = "move";
  ev.dataTransfer.setData("text/x-opengrab-tile", dashDrag);
});
document.addEventListener("dragover", (ev) => {
  const t = ev.target.closest?.(".dash-tile");
  if (!t || !dashDrag) return;
  ev.preventDefault(); ev.stopPropagation();
  $$(".dash-tile").forEach(x => x.classList.toggle("drop-before", x === t && x.dataset.tile !== dashDrag));
});
document.addEventListener("drop", (ev) => {
  const t = ev.target.closest?.(".dash-tile");
  if (!t || !dashDrag) return;
  ev.preventDefault(); ev.stopPropagation();
  const order = dashOrder().filter(k => k !== dashDrag);
  order.splice(order.indexOf(t.dataset.tile), 0, dashDrag);
  dashDrag = null;
  saveDashOrder(order);
});
document.addEventListener("dragend", () => { dashDrag = null; $$(".dash-tile").forEach(x => x.classList.remove("dragging", "drop-before")); });
document.addEventListener("click", (ev) => {
  const t = ev.target.closest(".tab[data-tab]");
  if (!t) return;
  clearInterval(dashTimer);
  if (t.dataset.tab === "dashboard") { loadDashboard(); dashTimer = setInterval(loadDashboard, 3000); }
});
window.addEventListener("resize", drawDashSpeed);

/* ============================================================ M14 Windows integration */
async function loadIntegration() {
  const box = $("#win-integration");
  if (!box) return;
  try {
    const st = await api("/api/system/integration");
    const inputs = $$("input", box);
    if (!st.available) {
      inputs.forEach(i => { i.disabled = true; });
      $("#win-int-note").textContent = "Link handlers and autostart are only available in the Windows app.";
      return;
    }
    $$("[data-proto]", box).forEach(i => { i.checked = !!st.protocols[i.dataset.proto]; });
    $$("[data-ftype]", box).forEach(i => { i.checked = !!st.files[i.dataset.ftype]; });
    $("#win-autostart").checked = !!st.autostart;
  } catch { /* banner shown on 401 */ }
}
$("#win-integration")?.addEventListener("change", async (ev) => {
  const i = ev.target;
  const body = i.dataset.proto ? { protocols: { [i.dataset.proto]: i.checked } }
    : i.dataset.ftype ? { files: { [i.dataset.ftype]: i.checked } } : { autostart: i.checked };
  try { await api("/api/system/integration", "POST", body); toast("Windows settings updated", "ok"); }
  catch (e) { i.checked = !i.checked; toast("Could not change it: " + e.message, "err"); }
});

/* ============================================================ M14 JDownloader 2 import */
function jd2Describe(f) {
  const parts = [`${f.unfinished} unfinished and ${f.downloads - f.unfinished} finished downloads`, `${f.linkgrabber} LinkGrabber links`];
  if (Object.keys(f.settings || {}).length) parts.push("settings");
  return `Found in ${f.folder}: ${parts.join(", ")}.` + (f.errors.length ? " " + f.errors.join(" ") : "");
}
$("#btn-jd2-find")?.addEventListener("click", async () => {
  const path = $("#jd2-path").value.trim();
  try {
    const r = await api("/api/import/jd2" + (path ? "?path=" + encodeURIComponent(path) : ""));
    if (!r.found.length) { $("#jd2-found").textContent = "JDownloader 2 was not found. Enter its folder above."; return; }
    if (!path) $("#jd2-path").value = r.found[0].folder;
    $("#jd2-found").textContent = jd2Describe(r.found[0]);
  } catch (e) { $("#jd2-found").textContent = e.message; }
});
$("#btn-jd2-import")?.addEventListener("click", async () => {
  const body = { path: $("#jd2-path").value.trim(), downloads: $("#jd2-downloads").checked, finished: $("#jd2-finished").checked,
    linkgrabber: $("#jd2-linkgrabber").checked, settings: $("#jd2-settings").checked };
  try {
    const r = await api("/api/import/jd2", "POST", body);
    $("#jd2-result").textContent = `Imported ${r.downloads} downloads and ${r.linkgrabber} LinkGrabber links; ${r.skipped} were already here or finished.`
      + (r.settings.length ? " Settings were updated." : "");
    toast("JDownloader 2 import finished", "ok");
    refreshData();
    if (r.settings.length) loadSettings();
  } catch (e) { $("#jd2-result").textContent = e.message; }
});


/* ============================================================ M14 updates */
function showUpdate(u) {
  $("#upd-status").textContent = `You have OpenGrab ${u.current}.`;
  $("#btn-upd-rollback").classList.toggle("hidden", !u.rollback);
  $("#btn-upd-install").classList.add("hidden");
  if (u.error) { $("#upd-status").textContent = u.error; return; }
  if (u.available === undefined) return;  // not checked yet
  if (!u.available) { $("#upd-status").textContent = `OpenGrab ${u.current} is the newest version.`; return; }
  let msg = `OpenGrab ${u.version} is available (you have ${u.current}).`;
  if (!u.frozen) msg += " This copy runs from source, so update it with git.";
  else if (!u.signing_ready) msg += " This build cannot verify updates, so download it from the releases page.";
  $("#upd-status").textContent = msg;
  $("#btn-upd-install").classList.toggle("hidden", !u.installable);
}
async function loadUpdate(check) {
  try { showUpdate(await api("/api/update" + (check ? "?check=1" : ""))); } catch (e) { $("#upd-status").textContent = e.message; }
}
$("#btn-upd-check")?.addEventListener("click", () => { $("#upd-status").textContent = "Checking…"; loadUpdate(true); });
$("#btn-upd-install")?.addEventListener("click", async () => {
  $("#upd-status").textContent = "Downloading and testing the update…";
  try { const r = await api("/api/update/install", "POST", {}); $("#upd-status").textContent = `Installing ${r.version}. OpenGrab restarts in a moment.`; }
  catch (e) { $("#upd-status").textContent = e.message; }
});
$("#btn-upd-rollback")?.addEventListener("click", async () => {
  if (!confirm("Go back to the previous version? OpenGrab restarts.")) return;
  try { await api("/api/update/rollback", "POST", {}); $("#upd-status").textContent = "Going back to the previous version. OpenGrab restarts in a moment."; }
  catch (e) { $("#upd-status").textContent = e.message; }
});
