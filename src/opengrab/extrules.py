"""Browser-extension interception rules (M12f).

Decides which browser downloads the OpenGrab extension hands off to the app:
file types, minimum size, per-site enable/disable and a bypass key. Rules are
stored as JSON and served to the extension at GET /api/extension/config.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

BYPASS_KEYS = ("alt", "shift", "ctrl", "none")

DEFAULT_FILE_TYPES = (
    "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "img", "exe", "msi",
    "dmg", "pkg", "deb", "rpm", "apk", "bin", "mp4", "mkv", "avi", "mov",
    "webm", "mp3", "flac", "wav", "m4a", "pdf", "torrent",
)

DEFAULT_RULES = {
    "enabled": True,
    "file_types": list(DEFAULT_FILE_TYPES),
    "min_size_bytes": 1024 * 1024,
    "sites": {},
    "bypass_key": "alt",
}


def _clean_ext(value) -> str:
    return str(value).strip().lower().lstrip(".")


def _clean_host(value) -> str:
    host = str(value).strip().lower()
    if "://" in host:
        host = urlsplit(host).hostname or ""
    return host.strip(".")


def normalize_rules(data) -> dict:
    """Return a complete, validated rules dict built on top of the defaults."""
    rules = json.loads(json.dumps(DEFAULT_RULES))
    if not isinstance(data, dict):
        return rules
    if "enabled" in data:
        rules["enabled"] = bool(data["enabled"])
    types = data.get("file_types")
    if isinstance(types, (list, tuple)):
        seen = []
        for t in types:
            e = _clean_ext(t)
            if e and e not in seen:
                seen.append(e)
        rules["file_types"] = seen
    if "min_size_bytes" in data:
        try:
            rules["min_size_bytes"] = max(0, int(data["min_size_bytes"]))
        except (TypeError, ValueError):
            pass
    sites = data.get("sites")
    if isinstance(sites, dict):
        rules["sites"] = {
            h: bool(v) for h, v in ((_clean_host(k), v) for k, v in sites.items()) if h
        }
    key = str(data.get("bypass_key", rules["bypass_key"])).strip().lower()
    if key in BYPASS_KEYS:
        rules["bypass_key"] = key
    return rules


def load_rules(path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return normalize_rules(json.load(fh))
    except (OSError, ValueError):
        return normalize_rules(None)


def save_rules(path, data) -> dict:
    rules = normalize_rules(data)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rules, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return rules


def host_of(url) -> str:
    try:
        return (urlsplit(str(url)).hostname or "").lower().strip(".")
    except ValueError:
        return ""


def _host_matches(host: str, rule_host: str) -> bool:
    return host == rule_host or host.endswith("." + rule_host)


def site_enabled(rules: dict, host: str):
    """True/False if a site rule applies to host (most specific wins), else None."""
    best = None
    best_len = -1
    for rule_host, enabled in rules.get("sites", {}).items():
        if _host_matches(host, rule_host) and len(rule_host) > best_len:
            best, best_len = enabled, len(rule_host)
    return best


def extension_of(filename_or_url) -> str:
    path = str(filename_or_url or "")
    if "://" in path:
        path = urlsplit(path).path
    name = path.rsplit("/", 1)[-1].split("?", 1)[0]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def should_intercept(rules, url, filename=None, size=None, page_url=None,
                     bypass_pressed=False) -> bool:
    """Decide whether a browser download should be handed to OpenGrab."""
    rules = normalize_rules(rules)
    if not rules["enabled"]:
        return False
    if bypass_pressed and rules["bypass_key"] != "none":
        return False
    for candidate in (page_url, url):
        host = host_of(candidate) if candidate else ""
        if host:
            verdict = site_enabled(rules, host)
            if verdict is False:
                return False
    ext = extension_of(filename) or extension_of(url)
    if rules["file_types"] and ext not in rules["file_types"]:
        return False
    if size is not None:
        try:
            if 0 <= int(size) < rules["min_size_bytes"]:
                return False
        except (TypeError, ValueError):
            pass
    return True


def scope_cookies(cookies, url) -> list:
    """Keep only cookies whose domain covers the download URL's host."""
    host = host_of(url)
    if not host:
        return []
    kept = []
    for c in cookies or []:
        if not isinstance(c, dict) or "name" not in c:
            continue
        domain = _clean_host(c.get("domain") or host)
        if _host_matches(host, domain):
            kept.append({"name": str(c["name"]), "value": str(c.get("value", "")),
                         "domain": domain, "path": str(c.get("path") or "/")})
    return kept


def public_config(rules) -> dict:
    """Payload for GET /api/extension/config."""
    rules = normalize_rules(rules)
    return {"version": 1, "rules": rules}


def browser_request_extras(extra, url) -> tuple:
    """(headers, cookies) for url from link.extra["browser"] set by the extension.

    headers holds Referer and User-Agent when known; cookies maps name to value
    for cookies whose domain covers url's host. Bad or missing data gives ({}, {}).
    """
    info = extra.get("browser") if isinstance(extra, dict) else None
    if not isinstance(info, dict):
        return {}, {}
    headers = {}
    ref = str(info.get("referrer") or "").strip()
    if ref.lower().startswith(("http://", "https://")):
        headers["Referer"] = ref
    ua = str(info.get("user_agent") or "").strip()
    if ua and "\n" not in ua and "\r" not in ua:
        headers["User-Agent"] = ua
    cookies = {}
    for c in scope_cookies(info.get("cookies") or [], url):
        cookies[c["name"]] = c["value"]
    return headers, cookies
