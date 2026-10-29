// OpenGrab interception rules (M12f). Mirrors src/opengrab/extrules.py should_intercept.
(function (root) {
  const DEFAULT_RULES = {
    enabled: true,
    file_types: ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "img", "exe", "msi",
      "dmg", "pkg", "deb", "rpm", "apk", "bin", "mp4", "mkv", "avi", "mov",
      "webm", "mp3", "flac", "wav", "m4a", "pdf", "torrent"],
    min_size_bytes: 1024 * 1024,
    sites: {},
    bypass_key: "alt",
  };

  function hostOf(url) {
    try { return new URL(String(url)).hostname.toLowerCase().replace(/^\.+|\.+$/g, ""); }
    catch (e) { return ""; }
  }

  function hostMatches(host, ruleHost) {
    return host === ruleHost || host.endsWith("." + ruleHost);
  }

  function siteEnabled(rules, host) {
    let best = null, bestLen = -1;
    const sites = rules.sites || {};
    for (const ruleHost of Object.keys(sites)) {
      if (hostMatches(host, ruleHost) && ruleHost.length > bestLen) {
        best = !!sites[ruleHost];
        bestLen = ruleHost.length;
      }
    }
    return best;
  }

  function extensionOf(nameOrUrl) {
    let path = String(nameOrUrl || "");
    if (path.indexOf("://") >= 0) {
      try { path = new URL(path).pathname; } catch (e) { return ""; }
    }
    path = path.replace(/\\/g, "/");
    const name = path.split("/").pop().split("?")[0];
    if (name.indexOf(".") < 0) return "";
    return name.split(".").pop().toLowerCase();
  }

  function normalize(rules) {
    const r = JSON.parse(JSON.stringify(DEFAULT_RULES));
    if (!rules || typeof rules !== "object") return r;
    if ("enabled" in rules) r.enabled = !!rules.enabled;
    if (Array.isArray(rules.file_types)) {
      r.file_types = [];
      for (const t of rules.file_types) {
        const e = String(t).trim().toLowerCase().replace(/^\.+/, "");
        if (e && r.file_types.indexOf(e) < 0) r.file_types.push(e);
      }
    }
    if ("min_size_bytes" in rules) {
      const n = parseInt(rules.min_size_bytes, 10);
      if (!isNaN(n)) r.min_size_bytes = Math.max(0, n);
    }
    if (rules.sites && typeof rules.sites === "object") r.sites = Object.assign({}, rules.sites);
    if (["alt", "shift", "ctrl", "none"].indexOf(rules.bypass_key) >= 0) r.bypass_key = rules.bypass_key;
    return r;
  }

  // opts: { url, filename, size, pageUrl, bypassPressed }
  function shouldIntercept(rules, opts) {
    const r = normalize(rules);
    const o = opts || {};
    if (!r.enabled) return false;
    if (o.bypassPressed && r.bypass_key !== "none") return false;
    for (const candidate of [o.pageUrl, o.url]) {
      const host = candidate ? hostOf(candidate) : "";
      if (host && siteEnabled(r, host) === false) return false;
    }
    const ext = extensionOf(o.filename) || extensionOf(o.url);
    if (r.file_types.length && r.file_types.indexOf(ext) < 0) return false;
    if (o.size !== undefined && o.size !== null) {
      const n = Number(o.size);
      if (!isNaN(n) && n >= 0 && n < r.min_size_bytes) return false;
    }
    return true;
  }

  const api = { DEFAULT_RULES, hostOf, extensionOf, siteEnabled, normalize, shouldIntercept };
  root.OpenGrabRules = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof self !== "undefined" ? self : globalThis);
