"""W1: notification center (M11f module) wired into the running app.

Replaces the server's in-memory deque. Notifications are persisted to
``notifications.json`` in the data dir, carry read state, raise a desktop
toast through ``toaster`` (set by the GUI tray) and are pushed to every live
client as a ``notification`` event.

The list endpoint keeps the M3 response shape (a JSON list, newest first,
with ``kind``, ``message`` and ``timestamp``) and adds ``title``, ``read``
and ``link_id``.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

from ..notifications import NotificationCenter, captcha_waiting_notification

log = logging.getLogger("opengrab.notify")

# link status -> (center kind, title)
_LINK_EVENTS = {
    "finished": ("finished", "Download finished"),
    "error": ("failed", "Download failed"),
    "captcha": ("info", "Captcha needed"),
    "login": ("info", "Login needed"),
    "paywall": ("info", "Paywall"),
}


class NotifyService:
    def __init__(self, srv, data_dir: Path):
        self.srv = srv
        self.path = Path(data_dir) / "notifications.json"
        self._lock = threading.RLock()
        self.toaster: Optional[Callable[[str, str], None]] = None
        self.center = NotificationCenter(max_items=200, toaster=self._toast,
                                         toasts_enabled=True)
        self._link_ids: dict[int, str] = {}
        self._labels: dict[int, str] = {}
        self._last_parked: dict[str, str] = {}  # link id -> last parked label
        self._load()

    # -- persistence ----------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            log.warning("notifications.json unreadable; starting empty")
            return
        items = raw.get("items", []) if isinstance(raw, dict) else []
        try:
            self.center.load_list(items)
        except (KeyError, TypeError, ValueError):
            log.warning("notifications.json has bad entries; starting empty")
            return
        for d in items:
            try:
                nid = int(d["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if d.get("link_id"):
                self._link_ids[nid] = str(d["link_id"])
            if d.get("label"):
                self._labels[nid] = str(d["label"])

    def _save(self) -> None:
        data = {"items": [dict(n, link_id=self._link_ids.get(n["id"], ""),
                               label=self._labels.get(n["id"], ""))
                          for n in self.center.to_list()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".notifications-", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.path)
        except OSError:
            log.warning("could not save notifications", exc_info=True)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # -- toasts -----------------------------------------------------------
    def _toast(self, title: str, message: str) -> None:
        settings = self.srv.manager.settings
        if not getattr(settings, "toasts_enabled", True):
            return
        if self.toaster is not None:
            self.toaster(title, message)

    # -- public -------------------------------------------------------------
    def add(self, kind: str, title: str, message: str = "", link_id: str = "",
            label: str = "") -> dict:
        with self._lock:
            note = self.center.add(kind, title, message)
            if link_id:
                self._link_ids[note.id] = link_id
            if label:
                self._labels[note.id] = label
            # forget mappings for notes that fell off the end
            live = {n.id for n in self.center._items}
            for d in (self._link_ids, self._labels):
                for k in [k for k in d if k not in live]:
                    del d[k]
            self._save()
            public = self._public(note.__dict__)
            unread = self.center.unread_count()
        self.srv.broadcast("notification", {"item": public, "unread": unread})
        return public

    def captcha_waiting(self, host: str, kind: str, link_id: str = "") -> dict:
        return self.add(*captcha_waiting_notification(host, kind), link_id=link_id,
                        label="captcha")

    def _public(self, n: dict) -> dict:
        nid = n["id"]
        return {
            "id": nid, "kind": self._labels.get(nid) or n["kind"], "level": n["kind"],
            "title": n["title"], "message": n["message"], "time": n["time"],
            "timestamp": n["time"], "read": n["read"],
            "link_id": self._link_ids.get(nid, ""),
        }

    def items(self) -> list[dict]:
        with self._lock:
            return [self._public(n.__dict__) for n in self.center.items()]

    def unread(self) -> int:
        with self._lock:
            return self.center.unread_count()

    def mark_read(self, nid: int) -> bool:
        with self._lock:
            ok = self.center.mark_read(nid)
            if ok:
                self._save()
        return ok

    def mark_all_read(self) -> None:
        with self._lock:
            self.center.mark_all_read()
            self._save()
        self.srv.broadcast("notification", {"item": None, "unread": 0})

    def clear(self) -> int:
        with self._lock:
            n = len(self.center._items)
            self.center.clear()
            self._link_ids.clear()
            self._labels.clear()
            self._save()
        self.srv.broadcast("notification", {"item": None, "unread": 0})
        return n

    # -- engine events ----------------------------------------------------
    def on_link_event(self, kind: str, payload) -> None:
        if not isinstance(payload, dict):
            return
        status = payload.get("status") or ""
        status = str(getattr(status, "value", status))
        link_id = str(payload.get("id") or "")
        parked = status in ("captcha", "login", "paywall")
        if link_id and not parked and status:
            self._last_parked.pop(link_id, None)  # left the parked state
        label = kind if kind in ("finished", "error") else status
        if label not in _LINK_EVENTS:
            return
        if kind not in ("finished", "error", "grabbed", "queued"):
            return  # parked states are announced when the grab parks the link
        if label in ("captcha", "login", "paywall"):
            if link_id and self._last_parked.get(link_id) == label:
                return  # already announced; a failed retry parks it again
            self._last_parked[link_id] = label
        center_kind, title = _LINK_EVENTS[label]
        name = (payload.get("title") or payload.get("filename") or "").strip()
        if not name and payload.get("url"):
            from urllib.parse import urlparse
            u = urlparse(str(payload["url"]))
            name = (u.path.rstrip("/").rsplit("/", 1)[-1] or u.netloc or str(payload["url"]))[:120]
        message = f"{name}: {label}" if name else f"Download {label}"
        if label == "error" and payload.get("error"):
            message += " (" + str(payload["error"])[:200] + ")"
        if label == "captcha" and self.srv.features.get("captcha") is not None:
            return  # W2 announces parked captchas as "Captcha waiting"
        self.add(center_kind, title, message, link_id=link_id, label=label)


def setup(srv):
    from ..settings import app_data_dir
    svc = NotifyService(srv, app_data_dir())
    mgr = srv.manager
    prev = mgr.on_change

    def chained(kind, payload):
        prev(kind, payload)
        try:
            svc.on_link_event(kind, payload)
        except Exception:
            log.debug("notification hook failed", exc_info=True)

    mgr.on_change = chained
    r = srv.router
    r.get(r"/api/notifications", lambda _req: svc.items())
    r.get(r"/api/notifications/summary", lambda _req: {"unread": svc.unread()})
    r.post(r"/api/notifications/clear", lambda _req: {"cleared": svc.clear()})
    r.post(r"/api/notifications/read-all", lambda _req: (svc.mark_all_read(), {"ok": True})[1])

    def read_one(req):
        ok = svc.mark_read(int(req.params["nid"]))
        return (200, {"ok": True}) if ok else (404, {"error": "notification not found"})

    r.post(r"/api/notifications/(?P<nid>\d+)/read", read_one)
    return svc
