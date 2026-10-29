"""Scheduler (M11a): time windows that allow, pause or speed-limit downloads."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

ACTIONS = ("allow", "pause", "limit")
ALL_DAYS = frozenset(range(7))  # Monday=0 ... Sunday=6


@dataclass(frozen=True)
class Window:
    start: time
    end: time
    days: frozenset = ALL_DAYS
    action: str = "allow"
    speed_kbps: int = 0

    def contains(self, now: datetime) -> bool:
        """True if now is inside the window. An overnight window (start > end) belongs to the day it starts on."""
        t = now.time()
        if self.start <= self.end:
            return now.weekday() in self.days and self.start <= t < self.end
        if t >= self.start:
            return now.weekday() in self.days
        return (now.weekday() - 1) % 7 in self.days and t < self.end


def parse_hhmm(s: str) -> time:
    parts = s.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"bad time {s!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"bad time {s!r}")
    return time(h, m)


def window_from_dict(d: dict) -> Window:
    action = d.get("action", "allow")
    if action not in ACTIONS:
        raise ValueError(f"bad action {action!r}")
    days = frozenset(int(x) for x in d.get("days", range(7)))
    if not days or not days <= ALL_DAYS:
        raise ValueError(f"bad days {sorted(days)!r}")
    speed = int(d.get("speed_kbps", 0))
    if speed < 0:
        raise ValueError("speed_kbps must be >= 0")
    return Window(parse_hhmm(d["start"]), parse_hhmm(d["end"]), days, action, speed)


def active_window(windows: list[Window], now: datetime) -> Window | None:
    """The last window in the list that contains now, or None."""
    hit = None
    for w in windows:
        if w.contains(now):
            hit = w
    return hit


def downloads_allowed(windows: list[Window], now: datetime) -> bool:
    w = active_window(windows, now)
    return w is None or w.action != "pause"


def speed_limit_kbps(windows: list[Window], now: datetime) -> int:
    """0 means unlimited."""
    w = active_window(windows, now)
    return w.speed_kbps if w is not None and w.action == "limit" else 0