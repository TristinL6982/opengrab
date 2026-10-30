"""M14 dashboard: today's totals, the last 14 days, a 5-minute speed graph,
the active downloads and the busiest sites.

GET /api/dashboard   everything the dashboard tiles show
Tile order is the ``dashboard_tiles`` setting (saved via /api/settings).
"""
from __future__ import annotations

import collections
import threading
import time
from urllib.parse import urlsplit

from ..engine.stats import DailyStats

SAMPLE_SECONDS = 2
SAMPLES = 150  # 5 minutes


class Dashboard:
    def __init__(self, srv, data_dir):
        self.srv = srv
        self.mgr = srv.manager
        self.stats = DailyStats(data_dir / "stats.json")
        self.speed = collections.deque(maxlen=SAMPLES)
        self._stop = threading.Event()
        self._moved = 0  # bytes counted since start; the graph uses its rate
        self._hook()
        self._thread = threading.Thread(target=self._sample, name="opengrab-dashboard", daemon=True)
        self._thread.start()

    def _hook(self) -> None:
        mgr = self.mgr
        orig = mgr._count_bytes
        stats = self.stats

        def counted(link_id, dl, done):
            before = mgr._tick_done.get(link_id)
            if before is None:
                before = int(getattr(dl, "start_done", 0) or 0)
            orig(link_id, dl, done)
            delta = mgr._tick_done.get(link_id, before) - before
            if delta > 0:
                try:
                    host = (urlsplit(dl.link.url).hostname or "").lower()
                except Exception:
                    host = ""
                stats.add_bytes(delta, host)
                self._moved += delta

        mgr._count_bytes = counted
        prev = mgr.on_change

        def chained(kind, payload):
            prev(kind, payload)
            if kind == "finished":
                stats.count("finished")
            elif kind == "error":
                stats.count("failed")

        mgr.on_change = chained

    def _sample(self) -> None:
        # bytes really written per interval, so a single reading of a fresh
        # connection's burst speed cannot draw a false spike
        last_t, last_b = time.monotonic(), self._moved
        while not self._stop.wait(SAMPLE_SECONDS):
            now, moved = time.monotonic(), self._moved
            bps = (moved - last_b) / max(now - last_t, 0.001)
            last_t, last_b = now, moved
            self.speed.append([int(time.time()), int(bps)])

    def snapshot(self) -> dict:
        out = self.stats.summary(14)
        links = self.mgr.all_links()
        active = [l for l in links if l.status.value == "downloading"]
        active.sort(key=lambda l: -(l.speed or 0))
        out["active"] = [{"id": l.id, "name": l.filename or l.title or l.url, "progress": l.progress,
                          "speed": l.speed, "eta": l.eta, "size": l.size} for l in active[:10]]
        counts: dict = collections.Counter(l.status.value for l in links)
        out["counts"] = dict(counts)
        out["speed_history"] = list(self.speed)
        out["tiles"] = list(getattr(self.mgr.settings, "dashboard_tiles", []) or [])
        return out

    def shutdown(self) -> None:
        self._stop.set()
        self.stats.save()


def setup(srv):
    from ..settings import app_data_dir
    dash = Dashboard(srv, app_data_dir())
    srv.router.get(r"/api/dashboard", lambda _req: dash.snapshot())
    return dash
