"""W3-W5: everything that decides *whether* and *how fast* a download may run.

FlowControl combines the M11 modules the integration audit found unwired:

* scheduler     - time windows that allow, pause or speed-limit downloads
* hostlimits    - at most N parallel downloads per host
* queues        - named queues with their own concurrency and pause switch
* speedprofiles - Normal/Gaming/Night profiles and a daily/monthly data cap
* categories    - Video/Music/Documents/Archives/Programs folders
* recovery      - one retry/backoff policy

The manager asks ``gate(link, active_links)`` before starting a download; a
non-empty answer is the reason it has to wait, shown in the UI. All config
lives in settings.json sections and is re-read by ``reload()``.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .. import scheduler as sched
from ..categories import CategoryRules
from ..hostlimits import HostLimits, host_of
from ..queues import DEFAULT_QUEUE, QueueManager
from ..recovery import RetryPolicy, policy_from_dict
from ..speedprofiles import DataCap, DataCapTracker, ProfileSet, SpeedProfile

log = logging.getLogger("opengrab.flow")


class FlowControl:
    def __init__(self, settings, data_dir: Path, clock: Callable[[], float] = time.time,
                 now: Callable[[], datetime] = datetime.now):
        self.settings = settings
        self.data_dir = Path(data_dir)
        self.clock = clock
        self.now = now
        self._lock = threading.RLock()
        self.errors: list[str] = []
        self.windows: list[sched.Window] = []
        self.host_limits = HostLimits()
        self.categories = CategoryRules()
        self.profiles = ProfileSet()
        self.queues = QueueManager()
        self.retry = RetryPolicy()
        self.tracker: Optional[DataCapTracker] = None
        self._cap_file = self.data_dir / "datacap.json"
        self._cap_dirty = 0
        self.reload()

    # -- config -----------------------------------------------------------
    def reload(self) -> list[str]:
        s = self.settings
        errors: list[str] = []
        windows = []
        for i, raw in enumerate(getattr(s, "scheduler_windows", None) or []):
            try:
                windows.append(sched.window_from_dict(raw))
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"schedule window {i + 1}: {exc}")
        try:
            hl = HostLimits(int(getattr(s, "max_per_host", 0) or 0),
                            dict(getattr(s, "per_host_limits", None) or {}))
        except (TypeError, ValueError) as exc:
            errors.append(f"per-host limits: {exc}")
            hl = HostLimits()
        try:
            cats = CategoryRules.from_list(getattr(s, "categories", None) or None)
        except (TypeError, ValueError, KeyError) as exc:
            errors.append(f"categories: {exc}")
            cats = CategoryRules()
        try:
            raw = getattr(s, "speed_profiles", None) or {}
            profiles = ProfileSet.from_dict(raw) if raw else ProfileSet()
        except (TypeError, ValueError, KeyError) as exc:
            errors.append(f"speed profiles: {exc}")
            profiles = ProfileSet()
        try:
            raw = getattr(s, "queues", None) or {}
            queues = QueueManager.from_dict(raw) if raw else QueueManager()
        except (TypeError, ValueError, KeyError) as exc:
            errors.append(f"queues: {exc}")
            queues = QueueManager()
        try:
            retry = policy_from_dict({"max_attempts": max(1, int(s.retry_max) + 1),
                                      **(getattr(s, "retry_policy", None) or {})})
        except (TypeError, ValueError) as exc:
            errors.append(f"retry policy: {exc}")
            retry = RetryPolicy()
        cap = None
        raw_cap = getattr(s, "data_cap", None) or {}
        if raw_cap.get("enabled") and raw_cap.get("limit_mb"):
            try:
                cap = DataCap(limit_bytes=int(float(raw_cap["limit_mb"]) * 1024 * 1024),
                              period=raw_cap.get("period", "day"),
                              action=raw_cap.get("action", "stop"),
                              slow_limit=int(raw_cap.get("slow_kbps", 64)) * 1024)
            except (TypeError, ValueError) as exc:
                errors.append(f"data cap: {exc}")
        with self._lock:
            self.windows, self.host_limits, self.categories = windows, hl, cats
            self.profiles, self.queues, self.retry = profiles, queues, retry
            if cap is None:
                self.tracker = None
            else:
                old = self.tracker
                self.tracker = DataCapTracker(cap)
                self._load_cap(old)
            self.errors = errors
        return errors

    def _load_cap(self, old: Optional[DataCapTracker]) -> None:
        t = self.tracker
        if t is None:
            return
        if old is not None:
            t._key, t._used = old._key, old._used
            t.history = dict(old.history)
            return
        try:
            raw = json.loads(self._cap_file.read_text(encoding="utf-8"))
            t._key = str(raw.get("key", ""))
            t._used = int(raw.get("used", 0))
            t.history = dict(raw.get("history", {}))
        except (OSError, ValueError, TypeError):
            pass

    def save_cap(self, force: bool = False) -> None:
        with self._lock:
            t = self.tracker
            if t is None or (not force and self._cap_dirty < 4 * 1024 * 1024):
                return
            self._cap_dirty = 0
            data = {"key": t._key, "used": t._used, "history": t.history}
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".datacap-", dir=str(self.data_dir))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self._cap_file)
        except OSError:
            log.debug("could not save datacap.json", exc_info=True)

    # -- speed --------------------------------------------------------------
    def schedule_window(self) -> Optional[sched.Window]:
        if not getattr(self.settings, "scheduler_enabled", False):
            return None
        return sched.active_window(self.windows, self.now())

    def effective_limit_bps(self) -> int:
        """Download limit in bytes/s from every source; 0 = unlimited."""
        limits = [max(0, int(getattr(self.settings, "speed_limit_kbps", 0) or 0)) * 1024]
        with self._lock:
            limits.append(int(self.profiles.active.download_limit or 0))
            w = self.schedule_window()
            if w is not None and w.action == "limit":
                limits.append(int(w.speed_kbps) * 1024)
            nonzero = [x for x in limits if x > 0]
            limit = min(nonzero) if nonzero else 0
            if self.tracker is not None:
                limit = self.tracker.effective_limit(limit, self.clock())
        return limit

    def record_bytes(self, n: int) -> None:
        if n <= 0:
            return
        with self._lock:
            if self.tracker is not None:
                self.tracker.add(n, self.clock())
                self._cap_dirty += n
        self.save_cap()

    def switch_profile(self, name: str) -> SpeedProfile:
        with self._lock:
            prof = self.profiles.switch(name)
            self.settings.speed_profiles = self.profiles.to_dict()
        return prof

    # -- gate -----------------------------------------------------------------
    def downloads_paused_by_schedule(self) -> bool:
        w = self.schedule_window()
        return w is not None and w.action == "pause"

    def queue_name(self, link) -> str:
        name = str((link.extra or {}).get("queue") or DEFAULT_QUEUE)
        try:
            return self.queues.get(name).name
        except KeyError:
            return DEFAULT_QUEUE

    def gate(self, link, active_links) -> str:
        """'' when the link may start now, otherwise the reason it waits."""
        if self.downloads_paused_by_schedule():
            return "Paused by the schedule"
        with self._lock:
            if self.tracker is not None and not self.tracker.allows_download(self.clock()):
                return "Data cap reached"
            qname = self.queue_name(link)
            q = self.queues.get(qname)
            if q.paused:
                return f"Queue {qname} is paused"
            if qname != DEFAULT_QUEUE:
                running = sum(1 for l in active_links if self.queue_name(l) == qname)
                if running >= q.max_active:
                    return f"Queue {qname} is full"
            url = link.direct_url or link.url
            limit = self.host_limits.limit_for(url)
            if limit > 0:
                host = host_of(url)
                running = sum(1 for l in active_links if host_of(l.direct_url or l.url) == host)
                if running >= limit:
                    return f"Waiting for a free slot on {host}"
        return ""

    # -- categories -----------------------------------------------------------
    def category_for(self, filename: str) -> str:
        with self._lock:
            return self.categories.category_for(filename)

    def category_subdir(self, filename: str) -> str:
        """Category folder relative to the download folder ('' for Other)."""
        if not getattr(self.settings, "categories_enabled", False):
            return ""
        with self._lock:
            name = self.categories.category_for(filename)
            cat = self.categories.get(name)
        if cat is None or os.path.isabs(cat.folder):
            return ""
        return cat.folder

    def status(self) -> dict:
        now = self.clock()
        w = self.schedule_window()
        with self._lock:
            cap = None
            if self.tracker is not None:
                cap = {"limit_bytes": self.tracker.cap.limit_bytes, "period": self.tracker.cap.period,
                       "action": self.tracker.cap.action, "used": self.tracker.used(now),
                       "remaining": self.tracker.remaining(now), "exceeded": self.tracker.exceeded(now)}
            return {
                "profiles": self.profiles.to_dict()["profiles"],
                "active_profile": self.profiles.active.name,
                "effective_limit_bps": self.effective_limit_bps(),
                "schedule": None if w is None else {"action": w.action, "start": w.start.strftime("%H:%M"),
                                                    "end": w.end.strftime("%H:%M"), "speed_kbps": w.speed_kbps},
                "data_cap": cap,
                "queues": [dict(q.to_dict(), items=None) for q in self.queues.queues.values()],
                "errors": list(self.errors),
            }
