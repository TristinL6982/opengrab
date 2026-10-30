"""M14 dashboard: transfer totals per day (bytes, finished, failed) and per
site, kept for 90 days in stats.json in the data dir."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

KEEP_DAYS = 90


class DailyStats:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()
        self._dirty = 0
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.days: dict = dict(raw.get("days", {}))
            self.hosts: dict = dict(raw.get("hosts", {}))
        except (OSError, ValueError):
            self.days, self.hosts = {}, {}

    def _today(self) -> str:
        return date.fromtimestamp(self.clock()).isoformat()

    def _day(self, key: Optional[str] = None) -> dict:
        key = key or self._today()
        return self.days.setdefault(key, {"bytes": 0, "finished": 0, "failed": 0})

    def add_bytes(self, n: int, host: str = "") -> None:
        if n <= 0:
            return
        with self._lock:
            self._day()["bytes"] += int(n)
            if host:
                self.hosts[host] = self.hosts.get(host, 0) + int(n)
            self._dirty += n
        if self._dirty >= 8 * 1024 * 1024:
            self.save()

    def count(self, what: str) -> None:
        with self._lock:
            d = self._day()
            d[what] = d.get(what, 0) + 1
            self._dirty += 1
        self.save()

    def save(self) -> None:
        with self._lock:
            cutoff = date.fromtimestamp(self.clock() - KEEP_DAYS * 86400).isoformat()
            self.days = {k: v for k, v in self.days.items() if k >= cutoff}
            if len(self.hosts) > 500:
                self.hosts = dict(sorted(self.hosts.items(), key=lambda kv: -kv[1])[:500])
            data = {"days": self.days, "hosts": self.hosts}
            self._dirty = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".stats-", dir=str(self.path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def summary(self, days: int = 14) -> dict:
        with self._lock:
            today = date.fromtimestamp(self.clock())
            series = []
            for i in range(days - 1, -1, -1):
                d = date.fromordinal(today.toordinal() - i).isoformat()
                v = self.days.get(d, {})
                series.append({"day": d, "bytes": v.get("bytes", 0), "finished": v.get("finished", 0),
                               "failed": v.get("failed", 0)})
            total = sum(v.get("bytes", 0) for v in self.days.values())
            top = sorted(self.hosts.items(), key=lambda kv: -kv[1])[:8]
        return {"today": series[-1], "days": series, "total_bytes": total,
                "top_hosts": [{"host": h, "bytes": b} for h, b in top],
                "generated": datetime.fromtimestamp(self.clock()).isoformat(timespec="seconds")}
