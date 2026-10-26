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
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

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
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
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
function connectSSE() {
  if (sseSource) sseSource.close();
  sseSource = new EventSource("/api/events");
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

function onEvent(kind, payload) {
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
  if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const maxBps = Math.max(1e6, ...S.graph.map(p => p.bps)) * 1.15;
  const now = Date.now();
  // grid: 4 lines
  ctx.strokeStyle = "#2c3140"; ctx.lineWidth = 1;
  for (let i = 1; i <= 3; i++) {
    const y = h - (h * i) / 4;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }
  // label of max
  ctx.fillStyle = "#8b93a7"; ctx.font = "10px monospace";
  ctx.fillText(humanBytes(maxBps) + "/s", 4, 10);
  if (S.graph.length < 2) return;
  const x = p => w - ((now - p.t) / 60000) * w;
  const y = b => h - (b / maxBps) * (h - 14);
  // area
  ctx.beginPath();
  ctx.moveTo(x(S.graph[0]), y(S.graph[0].bps));
  for (const p of S.graph) ctx.lineTo(x(p), y(p.bps));
  ctx.lineTo(x(S.graph[S.graph.length - 1]), h);
  ctx.lineTo(x(S.graph[0]), h);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(79,140,255,.35)");
  grad.addColorStop(1, "rgba(79,140,255,.02)");
  ctx.fillStyle = grad; ctx.fill();
  // line
  ctx.beginPath();
  ctx.moveTo(x(S.graph[0]), y(S.graph[0].bps));
  for (const p of S.graph) ctx.lineTo(x(p), y(p));
  ctx.strokeStyle = "#4f8cff"; ctx.lineWidth = 1.6; ctx.stroke();
  // current dot
  const last = S.graph[S.graph.length - 1];
  ctx.beginPath(); ctx.arc(x(last), y(last.bps), 2.5, 0, Math.PI * 2);
  ctx.fillStyle = "#38bdf8"; ctx.fill();
}
setInterval(renderGraph, 1000);

/* ============================================================ link rows */
const ACTIVE = ["waiting", "resolving", "grabbing", "downloading", "paused",
                "captcha", "login", "paywall", "error"];

function rowActions(l) {
  const b = [];
  const sm = (txt, act, cls = "") =>
    `<button class="sm ${cls}" data-act="${act}" data-id="${l.id}">${txt}</button>`;
  switch (l.status) {
    case "waiting":
      b.push(sm("start", "start"), sm("remove", "remove", "danger"));
      break;
    case "downloading":
      b.push(sm("pause", "pause"), sm("stop", "stop", "danger"));
      break;
    case "paused":
      b.push(sm("resume", "resume"), sm("stop", "stop", "danger"));
      break;
    case "error":
      b.push(sm("retry", "start"), sm("remove", "remove", "danger"));
      break;
    case "resolving":
    case "grabbing":
      b.push(sm("stop", "stop", "danger"));
      break;
    case "captcha":
      b.push(`<span class="inlineform" data-for="${l.id}"></span>`);
      break;
    case "login":
      b.push(`<span class="inlineform" data-for="${l.id}"></span>`);
      break;
    case "paywall":
      b.push(sm("try anyway", "paywall"));
      b.push(sm("remove", "remove", "danger"));
      break;
    case "finished":
      b.push(sm("remove", "remove", "danger"));
      break;
    default:
      b.push(sm("remove", "remove", "danger"));
  }
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
    <button class="sm" data-act="toggle-kids" data-id="${l.id}">${open ? "▾ hide" : "▸ show " + l.children.length + " children"}</button>
    ${open ? `<button class="sm" data-act="queue-children" data-id="${l.id}">queue all</button>` : ""}
    ${rows}
  </div>`;
}

function linkRow(l, showStatus = true) {
  const pct = Math.round((l.progress || 0) * 100);
  const barCls = l.status === "error" ? "bar err" : l.status === "paused" ? "bar paused" : "bar";
  return `<div class="linkrow" data-lid="${l.id}">
    <div class="c-title">
      ${showStatus ? `<span class="st ${l.status}">${l.status}</span>` : ""}
      <div class="t">${esc(l.title || l.filename || l.url)}</div>
      <div class="u">${esc(l.url)}${l.plugin ? " · " + esc(l.plugin) : ""}</div>
      ${l.status === "error" && l.error ? `<div class="u" style="color:var(--err)">${esc(l.error)}</div>` : ""}
      ${l.status === "paywall" && l.paywall_note ? `<div class="u" style="color:var(--warn)">${esc(l.paywall_note)}</div>` : ""}
      ${l.status === "finished" && l.path ? `<div class="u" style="color:var(--ok)">${esc(l.path)}</div>` : ""}
    </div>
    <div class="c-size">${l.size ? humanBytes(l.size) : (l.filename ? "" : "—")}</div>
    <div class="c-speed">${l.status === "downloading" ? humanBytes(l.speed) + "/s" : "—"}</div>
    <div class="c-eta">${l.status === "downloading" ? humanEta(l.eta) : (l.status === "finished" ? humanTime(l.finished) : "—")}</div>
    <div class="c-prog"><div class="${barCls}"><i style="width:${pct}%"></i></div><span class="pct">${pct}%</span></div>
    <div class="c-actions">${rowActions(l)}</div>
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
  }
  return arr.sort((a, b) => b.created - a.created);
}

function renderLists() {
  const q = $("#queue-list");
  const ql = queueLinks();
  q.innerHTML = ql.map(l => linkRow(l)).join("");
  $("#queue-empty").classList.toggle("hidden", ql.length > 0);
  const a = $("#all-list");
  a.innerHTML = allLinksFiltered().map(l => linkRow(l, false)).join("");
  renderInlineForms(document);
  renderAccounts();
  renderFilters();
}

let accountsCache = [], filtersCache = [];
async function renderAccounts() {
  if (!accountsCache.length && !$("#tab-accounts").classList.contains("active")) return;
  const el = $("#accounts-list");
  el.innerHTML = accountsCache.length
    ? accountsCache.map(a => `<div class="krow">
        <code>${esc(a.site)}</code>
        <span class="badge">user: ${esc(a.username)}</span>
        <span class="grow"></span>
        <button class="sm danger" data-act="acc-del" data-site="${esc(a.site)}">remove</button>
      </div>`).join("")
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

function refreshData() {
  api("/api/links").then(links => {
    S.links = new Map(links.map(l => [l.id, l]));
    renderLists();
    updatePills();
  }).catch(() => {});
  api("/api/stats").then(s => { S.stats = s; updatePills(); }).catch(() => {});
  api("/api/accounts").then(a => { accountsCache = a; renderAccounts(); }).catch(() => {});
  api("/api/filters").then(f => { filtersCache = f; renderFilters(); }).catch(() => {});
}

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
        await api(`/api/accounts/${encodeURIComponent(el.dataset.site)}`, "DELETE");
        accountsCache = [];
        refreshData();
        break;
      }
      case "flt-del": {
        await api(`/api/filters/${encodeURIComponent(el.dataset.id)}`, "DELETE");
        filtersCache = [];
        refreshData();
        break;
      }
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
$$(".tab").forEach(t => t.addEventListener("click", () => {
  $$(".tab").forEach(x => x.classList.remove("active"));
  $$(".tabpane").forEach(x => x.classList.remove("active"));
  t.classList.add("active");
  $("#tab-" + t.dataset.tab).classList.add("active");
  if (t.dataset.tab === "settings") loadSettings();
  refreshData();
  renderGraph();
}));

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
  try {
    const out = await api("/api/urls", "POST", {
      urls,
      priority: +$("#url-priority").value || 0,
      auto_download: $("#url-auto").checked,
    });
    const errs = (out || []).filter(o => o.error);
    if (errs.length) toast(errs.length + " link(s) failed: " + errs[0].error, "err");
    else toast("Added " + urls.length + " link(s)", "ok");
    ta.value = "";
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
$("#btn-acc-add").addEventListener("click", async () => {
  const site = $("#acc-site").value.trim();
  const user = $("#acc-user").value.trim();
  const pass = $("#acc-pass").value;
  if (!site || !user) return toast("Site and username required", "err");
  try {
    await api("/api/accounts", "POST", { site, username: user, password: pass });
    $("#acc-site").value = ""; $("#acc-user").value = ""; $("#acc-pass").value = "";
    toast("Account saved", "ok");
    refreshData();
  } catch (e) { toast("Failed: " + e.message, "err"); }
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
  "speed_limit_kbps", "retry_max", "impersonate", "proxy", "captcha_provider",
  "captcha_2captcha_key", "captcha_capmonster_key", "captcha_capsolver_key",
  "ua_rotation", "auto_start_after_grab"];

async function loadSettings() {
  try {
    const s = await api("/api/settings");
    for (const f of SET_FIELDS) {
      const el = $("#set-" + f);
      if (!el) continue;
      if (el.type === "checkbox") el.checked = !!s[f];
      else el.value = s[f] ?? "";
    }
    $("#set-confirm_delete").checked = !!s.confirm_delete;
    $("#token-display").textContent = s.api_token || "(local only)";
    $("#lan-url").textContent = s.api_host === "127.0.0.1"
      ? "http://<your-ip>:" + s.api_port + "/#token=" + s.api_token
      : "http://" + s.api_host + ":" + s.api_port + "/";
  } catch { /* banner shown on 401 */ }
}

$("#btn-set-save").addEventListener("click", async () => {
  const body = {};
  for (const f of SET_FIELDS) {
    const el = $("#set-" + f);
    if (!el) continue;
    body[f] = el.type === "checkbox" ? el.checked
      : ["max_parallel", "per_link_chunks", "speed_limit_kbps", "retry_max"].includes(f)
        ? +el.value : el.value;
  }
  body.confirm_delete = $("#set-confirm_delete").checked;
  try {
    await api("/api/settings", "POST", body);
    toast("Settings saved", "ok");
  } catch (e) { toast("Save failed: " + e.message, "err"); }
});

$("#btn-set-reload-plugins").addEventListener("click", async () => {
  try {
    const r = await api("/api/plugins/scan", "POST");
    toast("Plugin scan: " + (r?.scanned ?? 0) + " plugin(s)", "ok");
  } catch (e) { toast("Scan failed: " + e.message, "err"); }
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

/* ============================================================ boot */
(function boot() {
  getToken();
  updatePills();
  renderGraph();
  refreshData();
  connectSSE();
  // PWA service worker (best effort; http contexts only register on https/localhost)
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  if (location.hash.includes("token=")) history.replaceState(null, "", location.pathname);
  setInterval(() => { renderGraph(); }, 1000);
})();
