// OpenGrab content script (M12f): reports when a link is clicked while the bypass key is held.
(function () {
  function report(ev) {
    const held = { alt: !!ev.altKey, shift: !!ev.shiftKey, ctrl: !!(ev.ctrlKey || ev.metaKey) };
    if (!held.alt && !held.shift && !held.ctrl) return;
    try {
      chrome.runtime.sendMessage({ type: "opengrab-bypass", keys: held, at: Date.now() });
    } catch (e) { /* extension reloaded */ }
  }
  document.addEventListener("mousedown", report, true);
  document.addEventListener("click", report, true);
})();
