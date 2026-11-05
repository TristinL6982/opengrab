"""W8: LinkGrabber v2 - online check, website spider and Click'n'Load.

All three feed the existing review list (``srv._clipboard_candidates``):
nothing is downloaded until the user chooses Add. Entries carry their
source, package name, archive passwords and online state.

* linkcheck  - HEAD each new entry in the background (online/offline, size,
               file name).
* spider     - crawl a site with a depth limit, same-domain/include/exclude
               filters, file-type filter, robots.txt and a request delay.
* cnl/cnl_server - the Click'n'Load 2 listener on 127.0.0.1:9666. Browser
               pages may post to it from any origin, so it can only add
               entries for review, never start downloads or change settings.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from .. import linkcheck, spider
from ..cnl_server import make_server

log = logging.getLogger("opengrab.linkgrabber")


class LinkGrabberService:
    def __init__(self, srv):
        self.srv = srv
        self.mgr = srv.manager
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="linkcheck")
        self.jobs: dict[str, dict] = {}
        self._jobs_lock = threading.Lock()
        self.cnl = None
        self.cnl_status = {"listening": False, "port": None, "error": ""}

    # -- review list --------------------------------------------------------
    def add(self, urls, source: str, package: str = "", passwords=None) -> list[str]:
        """Queue URLs for review. Returns the new candidate ids."""
        existing = {l.url for l in self.mgr.all_links()}
        added = []
        with self.srv._clipboard_candidates_lock:
            known = {c["url"] for c in self.srv._clipboard_candidates.values()}
            for raw in urls or []:
                url = str(raw).strip().rstrip(".,;:!?)]}")
                if not url.lower().startswith(("http://", "https://", "ftp://", "ftps://", "magnet:?")):
                    continue
                if url in existing or url in known:
                    continue
                allowed, _ = self.mgr.filters.allows(url, "")
                if not allowed:
                    continue
                cid = uuid.uuid4().hex
                self.srv._clipboard_candidates[cid] = {
                    "id": cid, "url": url, "seen_at": time.time(), "source": source,
                    "package": package or "", "passwords": list(passwords or []),
                    "state": "unchecked", "size": None, "filename": "",
                }
                known.add(url)
                added.append(cid)
            while len(self.srv._clipboard_candidates) > 2000:
                self.srv._clipboard_candidates.pop(next(iter(self.srv._clipboard_candidates)))
        if added:
            self.srv.broadcast("linkgrabber", {"added": len(added), "source": source})
            self.check(added)
        return added

    # -- online check -------------------------------------------------------
    def _head(self, url: str):
        client = self.mgr.client
        r = client.request("HEAD", url, timeout=15, detect_challenges=False)
        if r.status_code in (403, 405, 501):
            r = client.get(url, timeout=15, max_bytes=4096, detect_challenges=False,
                           headers={"Range": "bytes=0-0"})
            headers = dict(r.headers)
            cr = headers.get("content-range", "")
            if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
                headers["content-length"] = cr.rsplit("/", 1)[1]
            return (200 if r.status_code == 206 else r.status_code), headers
        return r.status_code, r.headers

    def check(self, ids=None) -> int:
        if not getattr(self.mgr.settings, "linkgrabber_online_check", True):
            return 0
        with self.srv._clipboard_candidates_lock:
            items = [dict(c) for cid, c in self.srv._clipboard_candidates.items()
                     if ids is None or cid in ids]
        items = [c for c in items if not c["url"].lower().startswith("magnet:")]
        for c in items:
            self._pool.submit(self._check_one, c["id"], c["url"])
        return len(items)

    def _check_one(self, cid: str, url: str) -> None:
        res = linkcheck.check_link(url, self._head)
        with self.srv._clipboard_candidates_lock:
            c = self.srv._clipboard_candidates.get(cid)
            if c is None:
                return
            # an error page's length is not the file size
            c.update(state=res.state, size=res.size if res.state == "online" else None,
                     status_code=res.status_code)
            if res.filename:
                c["filename"] = res.filename
        self.srv.broadcast("linkgrabber", {"checked": cid, "state": res.state})

    # -- spider ----------------------------------------------------------------
    def start_spider(self, body: dict) -> dict:
        url = str(body.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("enter an http(s) address to start from")
        s = self.mgr.settings
        exts = body.get("extensions") or []
        if isinstance(exts, str):
            exts = exts.replace(",", " ").split()
        opts = spider.SpiderOptions(
            max_depth=max(0, min(int(body.get("depth", 1)), 5)),
            same_domain=bool(body.get("same_domain", True)),
            extensions=tuple(e if e.startswith(".") else "." + e for e in exts) or spider_default_exts(),
            include=tuple(x for x in (body.get("include") or []) if x),
            exclude=tuple(x for x in (body.get("exclude") or []) if x),
            max_pages=max(1, min(int(body.get("max_pages") or s.spider_max_pages), 5000)),
            respect_robots=bool(body.get("respect_robots", s.spider_respect_robots)),
            delay=max(0.0, float(body.get("delay", s.spider_delay_s))),
        )
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "url": url, "status": "running", "pages": 0, "files": 0,
               "errors": 0, "blocked": 0, "added": 0, "started": time.time()}
        with self._jobs_lock:
            self.jobs[jid] = job

        def fetch(u):
            r = self.mgr.client.get(u, timeout=20, max_bytes=3_000_000, detect_challenges=False)
            if r.status_code >= 400:
                raise OSError(f"HTTP {r.status_code}")
            return (r.headers.get("content-type", "") or "", r.text)

        def run():
            try:
                result = spider.crawl(url, fetch, opts)
                ids = self.add(result["files"], "spider", package=urlparse(url).netloc)
                job.update(status="done", pages=len(result["pages"]), files=len(result["files"]),
                           errors=len(result["errors"]), blocked=len(result.get("blocked", [])),
                           added=len(ids))
            except Exception as exc:  # reported in the job
                job.update(status="failed", error=str(exc)[:300])
            job["finished"] = time.time()
            self.srv.broadcast("spider", dict(job))

        threading.Thread(target=run, name=f"spider-{jid}", daemon=True).start()
        return dict(job)

    # -- Click'n'Load ------------------------------------------------------------
    def on_cnl(self, result: dict) -> None:
        ids = self.add(result.get("links") or [], "clicknload", package=result.get("package", ""),
                       passwords=result.get("passwords") or [])
        notify = self.srv.features.get("notify")
        if ids and notify is not None:
            what = result.get("package") or result.get("source") or "a web page"
            notify.add("info", "Links from Click'n'Load", f"{len(ids)} link(s) from {what} wait in the LinkGrabber.",
                       label="linkgrabber")

    def start_cnl(self, port: int | None = None) -> dict:
        s = self.mgr.settings
        port = int(s.cnl_port if port is None else port)
        if self.cnl is not None:
            return dict(self.cnl_status)
        try:
            self.cnl = make_server(self.on_cnl, host="127.0.0.1", port=port)
        except OSError as exc:
            # C7 lesson: a busy port is a warning, never a startup failure
            self.cnl_status = {"listening": False, "port": port,
                               "error": f"port {port} is in use ({exc.strerror or exc})"}
            log.warning("Click'n'Load not started: %s", self.cnl_status["error"])
            return dict(self.cnl_status)
        self.cnl.daemon_threads = True
        threading.Thread(target=self.cnl.serve_forever, name="clicknload", daemon=True).start()
        self.cnl_status = {"listening": True, "port": self.cnl.server_address[1], "error": ""}
        return dict(self.cnl_status)

    def stop_cnl(self) -> None:
        if self.cnl is not None:
            self.cnl.shutdown()
            self.cnl.server_close()
            self.cnl = None
            self.cnl_status = dict(self.cnl_status, listening=False)

    def shutdown(self) -> None:
        self.stop_cnl()
        self._pool.shutdown(wait=False, cancel_futures=True)


def spider_default_exts() -> tuple:
    from ..plugins.builtin.direct import FILE_EXTS
    return tuple(e if e.startswith(".") else "." + e for e in FILE_EXTS)


def setup(srv):
    svc = LinkGrabberService(srv)

    def capture(text):
        """Copied links go the same way as pasted ones: filtered, de-duplicated,
        announced to the UI (so it can say so) and checked online."""
        if not srv.settings.clipboard_monitor_enabled or not isinstance(text, str) or not text:
            return 0
        import re
        found = re.findall(r"(?:https?://|ftps?://|magnet:\?)[^\s<>\"']+", text, flags=re.IGNORECASE)
        return len(svc.add(found, "clipboard"))

    srv.capture_clipboard_text = capture
    r = srv.router

    def add(req):
        urls = req.body.get("urls") or []
        if isinstance(urls, str):
            urls = urls.split()
        ids = svc.add(urls[:2000], str(req.body.get("source") or "paste"),
                      package=str(req.body.get("package") or ""))
        return 201, {"added": len(ids), "ids": ids}

    def add_all(req):
        from ..savedir import SaveDirError, error_payload, folder_from_body
        only_online = bool(req.body.get("only_online", False))
        try:
            save_dir = folder_from_body(srv.manager.settings, req.body)  # checked before anything leaves the list
        except SaveDirError as exc:
            return error_payload(exc)
        with srv._clipboard_candidates_lock:
            picked = [c for c in srv._clipboard_candidates.values()
                      if not only_online or c.get("state") == "online"]
            for c in picked:
                srv._clipboard_candidates.pop(c["id"], None)
        links = [srv.add_candidate_link(c, save_dir) for c in picked]
        return {"added": len(links)}

    def spider_start(req):
        return 202, svc.start_spider(req.body)

    def spider_job(req):
        job = svc.jobs.get(req.params["jid"])
        return (200, job) if job else (404, {"error": "no such spider job"})

    r.post(r"/api/linkgrabber", add)
    r.post(r"/api/linkgrabber/add-all", add_all)
    r.post(r"/api/linkgrabber/check", lambda _req: {"checking": svc.check()})
    r.post(r"/api/linkgrab/spider", spider_start)
    r.get(r"/api/linkgrab/spider/(?P<jid>[a-f0-9]{12})", spider_job)
    r.get(r"/api/cnl/status", lambda _req: dict(svc.cnl_status))
    return svc
