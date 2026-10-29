// OpenGrab MV3 background service worker (Chrome, Edge, Opera, Brave, Vivaldi).
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