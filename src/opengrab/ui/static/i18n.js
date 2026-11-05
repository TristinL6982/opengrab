/* OpenGrab UI translations (M14b). English is the source language; other
   languages map English strings (and a few patterns with numbers) in
   i18n/<lang>.json. Text, placeholders, titles and aria-labels are
   translated when the page loads and whenever the app adds content. */
"use strict";
(function () {
  const BASE = String(window.OPENGRAB_BASE_PATH || "").replace(/\/$/, "");
  const SUPPORTED = ["en", "zh-CN"];
  function pick() {
    let l = "auto";
    try { l = localStorage.getItem("og_lang") || "auto"; } catch {}
    if (l === "auto") l = (navigator.language || "en").toLowerCase().startsWith("zh") ? "zh-CN" : "en";
    return SUPPORTED.includes(l) ? l : "en";
  }
  const lang = pick();
  document.documentElement.lang = lang;
  let dict = {}, pats = [];
  if (lang !== "en") {
    try {
      const x = new XMLHttpRequest();
      x.open("GET", BASE + "/i18n/" + lang + ".json", false);  // small local file; needed before first paint
      x.send();
      if (x.status === 200) {
        const data = JSON.parse(x.responseText);
        dict = data.strings || {};
        pats = (data.patterns || []).map(([re, rep]) => [new RegExp(re), rep]);
      }
    } catch {}
  }
  function tr(s) {
    const k = String(s).replace(/\s+/g, " ").trim();
    if (!k) return null;
    if (Object.prototype.hasOwnProperty.call(dict, k)) return dict[k];
    for (const [re, rep] of pats) if (re.test(k)) return k.replace(re, rep);
    return null;
  }
  const SKIP = new Set(["SCRIPT", "STYLE", "CODE", "PRE", "TEXTAREA", "svg"]);
  const ATTRS = ["placeholder", "title", "aria-label"];
  const orig = new WeakMap();   // text node -> English original (used by settings search)
  function translate(root) {
    if (lang === "en" || !root) return;
    if (root.nodeType === 3) {
      const p = root.parentNode;
      if (p && !SKIP.has(p.nodeName) && !p.closest?.("code,pre,textarea,[data-no-i18n]")) {
        const v = root.nodeValue, r = tr(v);
        if (r !== null) {
          const k = v.replace(/\s+/g, " ").trim();
          const lead = v.match(/^\s*/)[0], trail = v.match(/\s*$/)[0];
          if (r !== k) { orig.set(root, k); root.nodeValue = lead + r + trail; }
        }
      }
      return;
    }
    if (root.nodeType !== 1 || SKIP.has(root.nodeName) || root.closest?.("[data-no-i18n]")) return;
    for (const a of ATTRS) {
      if (root.hasAttribute(a)) { const r = tr(root.getAttribute(a)); if (r !== null) root.setAttribute(a, r); }
    }
    for (const c of Array.from(root.childNodes)) translate(c);
  }
  window.OG_I18N = { lang, original: (n) => orig.get(n), t: (s) => { const r = tr(s); return r === null ? s : r; }, translate };
  if (lang === "en") return;
  translate(document.body);
  new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.type === "characterData") translate(m.target);
      else if (m.type === "attributes") translate(m.target);
      else m.addedNodes.forEach(translate);
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true,
                              attributes: true, attributeFilter: ATTRS });
})();
