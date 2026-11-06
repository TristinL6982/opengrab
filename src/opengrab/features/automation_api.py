"""M13: automation in the running app - event hooks, webhooks, Folder Watch,
anti-standby and the "when done" action (shut down, sleep, hibernate, exit)."""
from __future__ import annotations

import logging
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import automation as auto

log = logging.getLogger("opengrab.automation")

BUSY = {"waiting", "resolving", "grabbing", "downloading"}


class AutomationService:
    def __init__(self, srv):
        self.srv = srv
        self.mgr = srv.manager
        self.settings = self.mgr.settings
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="automation")
        self.history: list[dict] = []
        self._lock = threading.Lock()
        self._was_busy = False
        self._seen_links: set[str] = set()
        self._watch_seen: dict = {}
        self.awake = auto.StayAwake()
        self.countdown_until: float | None = None
        self.countdown_action = ""
        self.executor = lambda argv: subprocess.run(argv, check=False,
                                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.exit_app = None  # set by the GUI so "exit" closes OpenGrab
        from ..tls import urlopen
        self.webhook_opener = urlopen
        self._stop = threading.Event()
        threading.Thread(target=self._loop, name="automation", daemon=True).start()

    # -- events ------------------------------------------------------------
    def on_link_event(self, kind: str, payload) -> None:
        if not isinstance(payload, dict) or not payload.get("id"):
            return
        status = str(getattr(payload.get("status"), "value", payload.get("status") or ""))
        lid = payload["id"]
        if kind == "grabbed" and lid not in self._seen_links:
            self._seen_links.add(lid)
            self.emit("added", payload)
        if kind == "finished":
            self.emit("finished", payload)
        elif kind == "error" or (kind == "grabbed" and status == "error"):
            self.emit("failed", payload)
        elif kind == "virus":
            self.emit("virus", payload)
        if status in BUSY:
            self._was_busy = True

    def emit(self, event: str, data: dict) -> None:
        s = self.settings
        hooks, _ = auto.hooks_from(getattr(s, "event_hooks", None) or [])
        hooks_for = [h for h in hooks if h.enabled and h.event == event]
        hooks_on = getattr(s, "hooks_enabled", True)
        webhooks, _ = auto.webhooks_from(getattr(s, "webhooks", None) or [])
        if hooks_on:
            for h in hooks_for:
                self.pool.submit(self._run_hook, h, event, dict(data))
        for w in webhooks:
            if w.enabled and event in w.events:
                self.pool.submit(self._post, w, event, dict(data))

    def _record(self, item: dict) -> None:
        with self._lock:
            self.history.append(dict(item, time=time.time()))
            self.history = self.history[-100:]

    def _run_hook(self, hook, event, data) -> None:
        self._record(dict(auto.run_hook(hook, event, data), kind="hook"))

    def _post(self, w: auto.Webhook, event: str, data: dict) -> None:
        body = auto.webhook_body(event, data)
        headers = {"Content-Type": "application/json", "User-Agent": "OpenGrab webhook",
                   "X-OpenGrab-Event": event}
        if w.secret:
            headers["X-OpenGrab-Signature"] = auto.sign(w.secret, body)
        last = ""
        for attempt in range(3):
            try:
                req = urllib.request.Request(w.url, data=body, headers=headers, method="POST")
                with self.webhook_opener(req, timeout=10) as resp:
                    code = getattr(resp, "status", 200)
                if code < 300:
                    self._record({"kind": "webhook", "event": event, "url": w.url, "code": code})
                    return
                last = f"HTTP {code}"
            except Exception as exc:  # unreachable endpoint: retry with backoff
                last = str(exc)[:200]
            time.sleep(min(8, 2 ** attempt))
        self._record({"kind": "webhook", "event": event, "url": w.url, "code": None, "error": last})

    # -- background loop -----------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.wait(2.0):
            try:
                self.tick()
            except Exception:
                log.debug("automation tick failed", exc_info=True)

    def busy(self) -> bool:
        return any(l.status.value in BUSY for l in self.mgr.all_links())

    def tick(self) -> None:
        busy = self.busy()
        mode = getattr(self.settings, "anti_standby", "downloading")
        self.awake.hold(mode == "always" or (mode == "downloading" and busy))
        if busy:
            self._was_busy = True
            if self.countdown_until is not None:  # new work arrived: call it off
                self.cancel_countdown()
        elif self._was_busy:
            self._was_busy = False
            links = self.mgr.all_links()
            self.emit("all_done", {"finished": sum(l.status.value == "finished" for l in links),
                                   "failed": sum(l.status.value == "error" for l in links)})
            self._start_countdown()
        if self.countdown_until is not None and time.time() >= self.countdown_until:
            self._do_when_done()
        self._watch_folders()

    # -- when done -------------------------------------------------------------
    def _start_countdown(self) -> None:
        action = getattr(self.settings, "when_done", "nothing")
        if action == "nothing":
            return
        delay = max(5, int(getattr(self.settings, "when_done_delay_s", 60) or 60))
        self.countdown_action = action
        self.countdown_until = time.time() + delay
        notify = self.srv.features.get("notify")
        if notify is not None:
            what = {"exit": "close OpenGrab", "sleep": "put the PC to sleep",
                    "hibernate": "hibernate the PC", "shutdown": "shut down the PC"}[action]
            notify.add("info", "Downloads done", f"OpenGrab will {what} in {delay} seconds unless you cancel.",
                       label="automation")
        self.srv.broadcast("when_done", self.status_when_done())

    def cancel_countdown(self) -> bool:
        had = self.countdown_until is not None
        self.countdown_until = None
        self.countdown_action = ""
        self.srv.broadcast("when_done", self.status_when_done())
        return had

    def _do_when_done(self) -> None:
        action = self.countdown_action
        self.countdown_until = None
        self.countdown_action = ""
        if getattr(self.settings, "when_done_once", True):
            self.settings.when_done = "nothing"  # like IDM: one-time unless chosen otherwise
            try:
                self.settings.save()
            except Exception:
                pass
        self._record({"kind": "when_done", "action": action})
        if action == "exit":
            if self.exit_app is not None:
                self.exit_app()
            return
        argv = auto.when_done_command(action)
        if argv:
            self.executor(argv)

    def status_when_done(self) -> dict:
        left = None if self.countdown_until is None else max(0, int(self.countdown_until - time.time()))
        return {"action": getattr(self.settings, "when_done", "nothing"), "pending": self.countdown_action,
                "seconds_left": left}

    # -- Folder Watch --------------------------------------------------------------
    def _watch_folders(self) -> None:
        for entry in getattr(self.settings, "folder_watch", None) or []:
            if not isinstance(entry, dict) or not entry.get("enabled", True) or not entry.get("path"):
                continue
            folder = Path(str(entry["path"])).expanduser()
            for path in auto.scan_folder(folder, self._watch_seen):
                try:
                    self._import(path, str(entry.get("mode") or "review"))
                    auto.archive_watch_file(path)
                except Exception as exc:
                    log.warning("Folder Watch could not import %s: %s", path.name, exc)
                    self._record({"kind": "folder_watch", "file": path.name, "error": str(exc)[:200]})
                    try:
                        bad = path.parent / "failed"
                        bad.mkdir(exist_ok=True)
                        path.replace(bad / path.name)
                    except OSError:
                        pass

    def _import(self, path: Path, mode: str) -> int:
        count = 0
        lg = self.srv.features.get("linkgrabber")
        for job in auto.parse_watch_file(path):
            if job.torrent_file:
                self.mgr.add_torrent_file(job.torrent_file, title=path.stem)
                count += 1
                continue
            urls = list(job.urls)
            if job.container:
                from .. import grabber
                found = grabber.analyze_container(job.container[0], job.container[1],
                                                  [l.url for l in self.mgr.all_links()])
                for pkg in found.get("packages", []):  # RSDF; DLC/CCF raise ContainerError
                    urls += [l if isinstance(l, str) else l.get("url", "") for l in pkg.get("links", [])]
                urls = [u for u in urls if u]
            start = job.auto_start if job.auto_start is not None else mode == "queue"
            if start or lg is None:
                for u in urls:
                    link = self.mgr.add_url(u, auto_download=True)
                    if job.package:
                        link.extra["package"] = job.package
                    if job.passwords:
                        link.extra["archive_passwords"] = list(job.passwords)
                    count += 1
            else:
                count += len(lg.add(urls, "folderwatch", package=job.package, passwords=job.passwords))
        self._record({"kind": "folder_watch", "file": path.name, "links": count})
        return count

    def shutdown(self) -> None:
        self._stop.set()
        self.awake.close()
        self.pool.shutdown(wait=False, cancel_futures=True)


def setup(srv):
    svc = AutomationService(srv)
    mgr = srv.manager
    prev = mgr.on_change

    def chained(kind, payload):
        prev(kind, payload)
        try:
            svc.on_link_event(kind, payload)
        except Exception:
            log.debug("automation hook failed", exc_info=True)

    mgr.on_change = chained
    inbox = mgr.captcha_inbox
    prev_new = inbox.on_new

    def on_captcha(req):
        if prev_new is not None:
            prev_new(req)
        svc.emit("captcha", {"host": req.host, "kind": req.kind, "id": req.id})

    inbox.on_new = on_captcha
    r = srv.router

    def status(_req):
        s = mgr.settings
        _, herr = auto.hooks_from(s.event_hooks)
        _, werr = auto.webhooks_from(s.webhooks)
        return {"when_done": svc.status_when_done(), "anti_standby": s.anti_standby,
                "awake": svc.awake.active, "history": list(reversed(svc.history[-50:])),
                "errors": herr + werr, "events": list(auto.EVENTS)}

    def set_when_done(req):
        action = str(req.body.get("action") or "")
        if action not in auto.WHEN_DONE:
            return 400, {"error": "action must be one of " + ", ".join(auto.WHEN_DONE)}
        mgr.settings.when_done = action
        mgr.settings.save()
        if action == "nothing":
            svc.cancel_countdown()
        return svc.status_when_done()

    def test_event(req):
        event = str(req.body.get("event") or "finished")
        if event not in auto.EVENTS:
            return 400, {"error": "unknown event"}
        svc.emit(event, {"id": "test", "title": "OpenGrab test event", "url": "https://example.com/test.bin",
                         "status": "finished", "filename": "test.bin"})
        return {"ok": True}

    r.get(r"/api/automation", status)
    r.post(r"/api/automation/when-done", set_when_done)
    r.post(r"/api/automation/cancel", lambda _req: {"cancelled": svc.cancel_countdown()})
    r.post(r"/api/automation/test", test_event, action="settings_write")

    def check_settings(applied):
        s = mgr.settings
        _, herr = auto.hooks_from(s.event_hooks)
        _, werr = auto.webhooks_from(s.webhooks)
        return herr + werr

    srv.settings_hooks.append(check_settings)
    return svc
