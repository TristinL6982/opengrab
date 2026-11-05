"""W2: captcha inbox, challenge solving and the widget harvest page.

* The manager parks Cloudflare/DDoS-Guard/widget challenges in
  ``manager.captcha_inbox`` (one entry per host and kind, shared by every
  link waiting on it). A new entry raises a "Captcha waiting" notification,
  which reaches paired devices and the desktop toast.
* ``POST /api/captcha/<id>/browser`` opens the site in a visible Edge (CDP)
  or Camoufox window; the cf_clearance cookie it collects is stored and every
  waiting link resumes.
* ``POST /api/captcha/<id>/harvest-link`` returns a one-time URL to a page
  that renders the reCAPTCHA/hCaptcha/Turnstile widget and posts the token
  back. That page and its submit route are public but need the per-request
  key, because a browser tab or iframe cannot send the bearer token.
"""
from __future__ import annotations

import hmac
import logging
import secrets
import threading
import time

from ..captcha import harvest
from ..challenge import BROWSER_KINDS, WIDGET_KINDS

log = logging.getLogger("opengrab.captcha.feature")


class CaptchaService:
    def __init__(self, srv):
        self.srv = srv
        self.mgr = srv.manager
        self.inbox = self.mgr.captcha_inbox
        self._keys: dict[str, str] = {}  # request id -> harvest key
        self._busy: set[str] = set()     # request ids with a browser open
        self._verifying: set[str] = set()  # link ids a "Verify all" run is working on
        self._lock = threading.Lock()

    # -- events -------------------------------------------------------------
    def on_new(self, req) -> None:
        notify = self.srv.features.get("notify")
        lids = [l.id for l in self.mgr.links_for_captcha(req.id)]
        if notify is not None:
            notify.captcha_waiting(req.host, req.kind, link_id=lids[0] if lids else "")
        self.srv.broadcast("captcha", {"pending": len(self.inbox), "request": self.public(req)})

    def public(self, req) -> dict:
        d = req.to_dict()
        d["links"] = [{"id": l.id, "title": l.title or l.filename or l.url}
                      for l in self.mgr.links_for_captcha(req.id)]
        d["solve_in_browser"] = req.kind in BROWSER_KINDS or req.kind in WIDGET_KINDS
        d["harvest"] = req.kind in WIDGET_KINDS and bool(req.sitekey)
        d["browser_busy"] = req.id in self._busy
        d["expires_at"] = req.created_at + req.timeout_s
        return d

    def pending(self) -> list[dict]:
        return [self.public(r) for r in self.inbox.pending()]

    def _done(self) -> None:
        self.srv.broadcast("captcha", {"pending": len(self.inbox), "request": None})

    # -- actions ------------------------------------------------------------
    def solve(self, rid: str, answer: str) -> tuple[int, dict]:
        answer = (answer or "").strip()
        if not answer:
            return 400, {"error": "answer is required"}
        if not self.inbox.solve(rid, answer):
            return 404, {"error": "captcha request not pending"}
        resumed = self.mgr.captcha_request_solved(rid, answer)
        with self._lock:
            self._keys.pop(rid, None)
        self._done()
        return 200, {"ok": True, "resumed": resumed}

    def skip(self, rid: str) -> tuple[int, dict]:
        if not self.inbox.skip(rid):
            return 404, {"error": "captcha request not pending"}
        n = self.mgr.captcha_request_skipped(rid)
        self._done()
        return 200, {"ok": True, "links": n}

    def browser(self, rid: str, make=None) -> tuple[int, dict]:
        req = self.inbox.get(rid)
        if req is None or req.status != "pending":
            return 404, {"error": "captcha request not pending"}
        with self._lock:
            if rid in self._busy:
                return 409, {"error": "a browser window is already open for this captcha"}
            self._busy.add(rid)
        settings = self.mgr.settings

        def run():
            from .. import browser_solver as bs
            # one browser at a time: the automatic batches and this window share a lock
            batcher = getattr(self.mgr, "captcha_batcher", None)
            lock = batcher.browser_lock if batcher is not None else threading.Lock()
            have_lock = False
            try:
                lock.acquire()
                have_lock = True
                factory = make
                if factory is None:
                    factory = (bs.make_stealth_driver
                               if (settings.captcha_browser_engine or "edge").lower() == "camoufox"
                               else bs.make_driver)
                wait = max(60.0, float(settings.captcha_manual_timeout_s or 120))
                if req.kind in WIDGET_KINDS and req.sitekey:
                    # a captcha inside the site's own form: its answer goes back to
                    # the plugin (a clearance cookie would not satisfy the form)
                    token = bs.solve_widget_in_browser(req.page_url, req.kind, req.sitekey,
                                                       factory(), timeout=wait)
                    if token:
                        self.solve(rid, token)
                        self._report(req, True, "")
                    else:
                        self._report(req, False, "the browser window closed or timed out "
                                                 "before the check passed")
                    return
                c = bs.solve_in_browser(req.page_url, self.mgr.clearance, factory(), timeout=wait)
                if c is None:
                    self._report(req, False, "the browser window closed or timed out before the check passed")
                else:
                    self.solve(rid, "__CLEARANCE__")
                    self._report(req, True, "")
            except Exception as exc:  # BrowserSolverUnavailable and friends
                log.info("browser solve failed: %s", exc)
                self._report(req, False, str(exc)[:300])
            finally:
                if have_lock:
                    lock.release()
                with self._lock:
                    self._busy.discard(rid)
                self._done()

        threading.Thread(target=run, name=f"captcha-browser-{rid[:8]}", daemon=True).start()
        return 202, {"ok": True, "started": True}

    def verify_all(self) -> tuple[int, dict]:
        """Start visible batch solves for every pending widget captcha (the user asked):
        one browser page per (host, kind, sitekey), one token per waiting link."""
        batcher = getattr(self.mgr, "captcha_batcher", None)
        if batcher is None:
            return 409, {"error": "the browser solver is not available"}
        fresh = {}
        with self._lock:
            for key, g in self.mgr.widget_captcha_groups().items():
                links = [l for l in g["links"] if l.id not in self._verifying]
                if links:
                    fresh[key] = {**g, "links": links}
                    self._verifying.update(l.id for l in links)
        started = sum(len(g["links"]) for g in fresh.values())
        if started:
            threading.Thread(target=self._verify_run, args=(fresh, batcher),
                             name="captcha-verify-all", daemon=True).start()
        return 200, {"ok": True, "started": started, "groups": len(fresh)}

    def _verify_run(self, groups: dict, batcher) -> None:
        notify = self.srv.features.get("notify")
        for (host, kind, sitekey), g in groups.items():
            links, total, given = list(g["links"]), len(g["links"]), 0
            if notify is not None:
                notify.add("info", f"Verifying {total} download(s) on {host}",
                           "A browser window opens and closes by itself when the checks are done.",
                           label="captcha")
            for i in range(0, total, batcher.max_batch):
                chunk = links[i:i + batcher.max_batch]
                waiting, mx = list(chunk), threading.Lock()

                def on_token(token, waiting=waiting, mx=mx):
                    with mx:
                        link = waiting.pop(0) if waiting else None
                    if link is not None:
                        self.mgr.give_captcha_token(link, token)

                try:
                    given += batcher.run_now(g["page_url"], kind, sitekey, len(chunk), on_token, hidden=False)
                except Exception as exc:  # noqa: BLE001
                    log.info("verify all failed for %s: %s", host, exc)
                with self._lock:
                    self._verifying.difference_update(l.id for l in chunk)
            for rid in g["requests"]:       # a request with no parked link left is done
                if not self.mgr.links_for_captcha(rid):
                    self.inbox.solve(rid, "__VERIFIED__")
            if notify is not None:
                if given == total:
                    notify.add("info", f"Verified {given} of {total} on {host}",
                               "The downloads continue.", label="captcha")
                else:
                    notify.add("failed", f"Verified {given} of {total} on {host}",
                               "The rest still wait; press Verify all to try again.", label="captcha")
            self._done()

    def _report(self, req, ok: bool, error: str) -> None:
        notify = self.srv.features.get("notify")
        if notify is None:
            return
        if ok:
            notify.add("info", f"Challenge passed: {req.host}", "Waiting downloads were resumed.",
                       label="captcha")
        else:
            notify.add("failed", f"Challenge not solved: {req.host}", error, label="captcha")

    def harvest_link(self, rid: str) -> tuple[int, dict]:
        req = self.inbox.get(rid)
        if req is None or req.status != "pending":
            return 404, {"error": "captcha request not pending"}
        if req.kind not in WIDGET_KINDS or not req.sitekey:
            return 400, {"error": "this challenge has no widget sitekey; use Solve in browser"}
        key = secrets.token_hex(16)
        with self._lock:
            self._keys[rid] = key
        base = self.srv.base_path or ""
        return 200, {"url": f"{base}/api/captcha/harvest/{rid}?k={key}",
                     "note": "Many sites only accept tokens solved on their own domain; "
                             "if the download still fails, use Solve in browser."}

    def key_ok(self, rid: str, key: str) -> bool:
        with self._lock:
            expected = self._keys.get(rid, "")
        return bool(expected) and hmac.compare_digest(expected, key or "")

    def harvest_page(self, rid: str, key: str) -> tuple[int, str]:
        req = self.inbox.get(rid)
        if req is None or req.status != "pending" or not self.key_ok(rid, key):
            return 404, "captcha request not found or link expired"
        kind = {"turnstile": "turnstile", "recaptcha": "recaptcha", "hcaptcha": "hcaptcha"}[req.kind]
        base = self.srv.base_path or ""
        page = harvest.build_harvest_page(kind, req.sitekey, rid,
                                          submit_path=f"{base}/api/captcha/harvest/{rid}/solve?k={key}")
        return 200, page


def setup(srv):
    svc = CaptchaService(srv)
    svc.inbox.on_new = svc.on_new
    r = srv.router

    r.get(r"/api/captcha/inbox", lambda _req: {"pending": svc.pending(),
                                               "clearances": _clearances(srv.manager)})

    def solve(req):
        return svc.solve(str(req.body.get("id") or ""), str(req.body.get("answer") or ""))

    r.post(r"/api/captcha/solve", solve)
    r.post(r"/api/captcha/verify-all", lambda _req: svc.verify_all())
    r.post(r"/api/captcha/(?P<rid>[a-f0-9]{32})/skip", lambda req: svc.skip(req.params["rid"]))
    r.post(r"/api/captcha/(?P<rid>[a-f0-9]{32})/browser", lambda req: svc.browser(req.params["rid"]))
    r.post(r"/api/captcha/(?P<rid>[a-f0-9]{32})/harvest-link",
           lambda req: svc.harvest_link(req.params["rid"]))

    def retry(req):
        ok = srv.manager.retry_captcha(req.params["lid"])
        return (200, {"ok": True}) if ok else (404, {"error": "link is not waiting for a captcha"})

    r.post(r"/api/links/(?P<lid>[^/]+)/captcha-retry", retry)

    def forget(req):
        srv.manager.clearance.remove(req.params["host"])
        return {"ok": True}

    r.delete(r"/api/captcha/clearances/(?P<host>[A-Za-z0-9.-]+)", forget)

    # public (key-protected) harvest routes; see ApiServer.public_routes
    def page(handler, rid, query):
        code, body = svc.harvest_page(rid, (query.get("k") or [""])[0])
        handler._send(code, body.encode("utf-8"), "text/html; charset=utf-8",
                      extra={"Content-Security-Policy":
                             "default-src 'none'; script-src 'unsafe-inline' https://www.google.com "
                             "https://www.gstatic.com https://js.hcaptcha.com https://*.hcaptcha.com "
                             "https://challenges.cloudflare.com; frame-src https:; connect-src 'self' https:; "
                             "style-src 'unsafe-inline' https:; img-src https: data:"})

    def submit(handler, rid, query, body):
        if not svc.key_ok(rid, (query.get("k") or [""])[0]):
            return handler._err(403, "bad or expired harvest key")
        code, payload = svc.solve(rid, str(body.get("answer") or ""))
        return handler._json(payload, code)

    srv.public_routes.append(("GET", r"/api/captcha/harvest/(?P<rid>[a-f0-9]{32})", page))
    srv.public_routes.append(("POST", r"/api/captcha/harvest/(?P<rid>[a-f0-9]{32})/solve", submit))
    return svc


def _clearances(mgr) -> list[dict]:
    store = mgr.clearance
    out = []
    now = time.time()
    for host, c in list(getattr(store, "_clearances", {}).items()):
        if not c.is_expired(now):
            out.append({"host": host, "expires_at": c.expires_at})
    return out
