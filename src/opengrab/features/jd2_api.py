"""M14 routes: import downloads, LinkGrabber entries and settings from
JDownloader 2. Reading only; JD2's own files are never changed.

GET  /api/import/jd2?path=...   folders found (or the given one) with counts
POST /api/import/jd2            {path, settings, downloads, linkgrabber, finished}
"""
from __future__ import annotations

from pathlib import Path

from .. import jd2import


def _summary(exp: jd2import.JdExport) -> dict:
    unfinished = [l for l in exp.downloads if not l.finished]
    return {"folder": exp.folder, "downloads": len(exp.downloads), "unfinished": len(unfinished),
            "linkgrabber": len(exp.linkgrabber), "settings": exp.settings, "errors": exp.errors,
            "packages": sorted({l.package for l in exp.downloads + exp.linkgrabber if l.package})[:50]}


def apply_settings(srv, values: dict) -> list:
    mgr = srv.manager
    known = set(type(mgr.settings).model_fields)
    allowed = {k: v for k, v in values.items() if k in known}
    if not allowed:
        return []
    candidate = type(mgr.settings).model_validate({**mgr.settings.model_dump(), **allowed})
    for k in allowed:
        setattr(mgr.settings, k, getattr(candidate, k))
    try:
        mgr.settings.save()
    except Exception:
        pass
    for hook in getattr(srv, "settings_hooks", []):
        try:
            hook(sorted(allowed))
        except Exception:
            pass
    return sorted(allowed)


def setup(srv):
    mgr = srv.manager
    r = srv.router

    def detect(req):
        path = req.q("path").strip()
        folders = [Path(path)] if path else jd2import.candidate_dirs()
        return {"found": [_summary(jd2import.read_export(f)) for f in folders]}

    def run_import(req):
        body = req.body or {}
        path = str(body.get("path") or "").strip()
        if not path:
            found = jd2import.candidate_dirs()
            if not found:
                return 404, {"error": "no JDownloader 2 folder found; enter its folder"}
            path = str(found[0])
        exp = jd2import.read_export(path)
        if exp.errors and not (exp.downloads or exp.linkgrabber or exp.settings):
            return 400, {"error": "; ".join(exp.errors)}
        result = {"folder": exp.folder, "settings": [], "downloads": 0, "linkgrabber": 0,
                  "skipped": 0, "errors": list(exp.errors)}
        if body.get("settings", True) and exp.settings:
            try:
                result["settings"] = apply_settings(srv, exp.settings)
            except Exception as exc:
                result["errors"].append(f"settings: {exc}")
        existing = {l.url for l in mgr.all_links()}
        if body.get("downloads", True):
            for item in exp.downloads:
                if (item.finished and not body.get("finished", False)) or item.url in existing:
                    result["skipped"] += 1
                    continue
                link = mgr.add_url(item.url, auto_download=False, filename=item.name)
                link.extra["source"] = "jd2"
                if item.package:
                    link.extra["package"] = item.package
                existing.add(item.url)
                result["downloads"] += 1
        lg = srv.features.get("linkgrabber")
        if body.get("linkgrabber", True) and exp.linkgrabber:
            by_pkg: dict = {}
            for item in exp.linkgrabber:
                if item.url in existing:
                    result["skipped"] += 1
                    continue
                by_pkg.setdefault(item.package, []).append(item.url)
                existing.add(item.url)
            for pkg, urls in by_pkg.items():
                if lg is not None:
                    result["linkgrabber"] += len(lg.add(urls, "jd2", package=pkg))
                else:
                    for u in urls:
                        mgr.add_url(u, auto_download=False)
                        result["linkgrabber"] += 1
        mgr._persist()
        return result

    r.get(r"/api/import/jd2", detect, action="settings_write")
    r.post(r"/api/import/jd2", run_import, action="settings_write")
    return None
