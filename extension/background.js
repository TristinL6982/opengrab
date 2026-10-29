// OpenGrab MV3 background service worker (Chrome, Edge, Opera, Brave, Vivaldi).
importScripts("rules.js");
const MENU_ID = "opengrab-download";
const DEFAULTS = { appUrl: "http://127.0.0.1:5678", deviceToken: "" };

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_ID,
    title: "Download with OpenGrab",
    contexts: ["link", "page", "selection"],
  });
});

function urlsFromSelection(text) {
  return (String(text || "").match(/https?:\/\/[^\s"'<>]+/g) || []);
}

function urlsFromClick(info, tab) {
  if (info.linkUrl) return [info.linkUrl];
  if (info.selectionText) {
    const found = urlsFromSelection(info.selectionText);
    if (found.length) return found;
  }
  const page = info.pageUrl || (tab && tab.url) || "";
  return page ? [page] : [];
}

async function sendToOpenGrab(urls) {
  const s = await chrome.storage.local.get(DEFAULTS);
  const base = String(s.appUrl || DEFAULTS.appUrl).replace(/\/+$/, "");
  const headers = { "Content-Type": "application/json" };
  if (s.deviceToken) headers.Authorization = "Bearer " + s.deviceToken;
  const res = await fetch(base + "/api/urls", {
    method: "POST",
    headers,
    body: JSON.stringify({ urls, auto_download: false }),
  });
  if (!res.ok) throw new Error("OpenGrab answered HTTP " + res.status);
  return res.json();
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== MENU_ID) return;
  const urls = urlsFromClick(info, tab);
  if (!urls.length) return;
  try {
    await sendToOpenGrab(urls);
    chrome.action && chrome.action.setBadgeText && chrome.action.setBadgeText({ text: "" });
  } catch (e) {
    console.warn("OpenGrab handoff failed:", e && e.message);
  }
});

// -- M12f: intercept browser downloads by rules, with a hold-key bypass ------
const BYPASS_WINDOW_MS = 3000;
const RULES_TTL_MS = 60000;
let lastBypass = { at: 0, keys: {} };
let cachedRules = null;
let rulesFetchedAt = 0;
const fallbackUrls = new Set();

chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "opengrab-bypass") {
    lastBypass = { at: Number(msg.at) || Date.now(), keys: msg.keys || {} };
  }
});

async function appConnection() {
  const s = await chrome.storage.local.get(DEFAULTS);
  const base = String(s.appUrl || DEFAULTS.appUrl).replace(/\/+$/, "");
  const headers = { "Content-Type": "application/json" };
  if (s.deviceToken) headers.Authorization = "Bearer " + s.deviceToken;
  return { base, headers };
}

async function loadRules(force) {
  if (!force && cachedRules && Date.now() - rulesFetchedAt < RULES_TTL_MS) return cachedRules;
  try {
    const { base, headers } = await appConnection();
    const res = await fetch(base + "/api/extension/config", { method: "GET", headers });
    if (!res.ok) return null;
    const data = await res.json();
    cachedRules = OpenGrabRules.normalize(data && data.rules);
    rulesFetchedAt = Date.now();
    return cachedRules;
  } catch (e) {
    return null;
  }
}

function bypassHeld(rules) {
  const key = rules.bypass_key;
  if (!key || key === "none") return false;
  if (Date.now() - lastBypass.at > BYPASS_WINDOW_MS) return false;
  return !!lastBypass.keys[key];
}

function knownSize(item) {
  if (item.totalBytes > 0) return item.totalBytes;
  if (item.fileSize > 0) return item.fileSize;
  return null;
}

async function handOffDownload(item, url) {
  let cookies = [];
  try {
    cookies = (await chrome.cookies.getAll({ url })).map((c) => ({
      name: c.name, value: c.value, domain: c.domain, path: c.path,
    }));
  } catch (e) { cookies = []; }
  const { base, headers } = await appConnection();
  const name = String(item.filename || "").split(/[\\/]/).pop();
  const res = await fetch(base + "/api/extension/add", {
    method: "POST",
    headers,
    body: JSON.stringify({
      url,
      filename: name,
      referrer: item.referrer || "",
      page_url: item.referrer || "",
      user_agent: navigator.userAgent,
      cookies,
      auto_download: true,
    }),
  });
  if (!res.ok) throw new Error("OpenGrab answered HTTP " + res.status);
  return res.json();
}

chrome.downloads.onCreated.addListener(async (item) => {
  const url = item.finalUrl || item.url || "";
  if (!/^https?:\/\//i.test(url)) return;
  if (fallbackUrls.has(url)) { fallbackUrls.delete(url); return; }
  const rules = await loadRules(false);
  if (!rules) return; // app not reachable: leave the download to the browser
  const take = OpenGrabRules.shouldIntercept(rules, {
    url,
    filename: item.filename,
    size: knownSize(item),
    pageUrl: item.referrer,
    bypassPressed: bypassHeld(rules),
  });
  if (!take) return;
  try { await chrome.downloads.cancel(item.id); } catch (e) { return; }
  try {
    await handOffDownload(item, url);
    try { await chrome.downloads.erase({ id: item.id }); } catch (e) { /* already gone */ }
  } catch (e) {
    console.warn("OpenGrab handoff failed, restarting in browser:", e && e.message);
    fallbackUrls.add(url);
    chrome.downloads.download({ url });
  }
});

loadRules(true);
