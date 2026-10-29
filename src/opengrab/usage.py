"""Account usage rules (M10a): daily traffic quota and parallel limit per account."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

DAY = 86400


@dataclass
class UsageRule:
    daily_bytes: int = 0      # 0 means unlimited
    max_parallel: int = 0     # 0 means unlimited
    reset_hour_utc: int = 0   # hour of day (UTC, 0-23) when the daily quota resets

    def __post_init__(self):
        if self.daily_bytes < 0 or self.max_parallel < 0:
            raise ValueError("limits must be >= 0")
        if not 0 <= self.reset_hour_utc <= 23:
            raise ValueError("reset_hour_utc must be between 0 and 23")


def day_key(ts: float, reset_hour_utc: int = 0) -> int:
    """Index of the quota day containing ts; days start at reset_hour_utc."""
    return int((ts - reset_hour_utc * 3600) // DAY)


class UsageTracker:
    """Counts bytes downloaded per account per quota day."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._used: dict[str, tuple[int, int]] = {}  # account_id -> (day, bytes)

    def _today(self, rule: UsageRule) -> int:
        return day_key(self._clock(), rule.reset_hour_utc)

    def used(self, account_id: str, rule: UsageRule | None = None) -> int:
        rule = rule or UsageRule()
        day, n = self._used.get(account_id, (None, 0))
        return n if day == self._today(rule) else 0

    def record(self, account_id: str, nbytes: int, rule: UsageRule | None = None) -> int:
        if nbytes < 0:
            raise ValueError("nbytes must be >= 0")
        rule = rule or UsageRule()
        total = self.used(account_id, rule) + int(nbytes)
        self._used[account_id] = (self._today(rule), total)
        return total

    def remaining(self, account_id: str, rule: UsageRule) -> int | None:
        if rule.daily_bytes == 0:
            return None
        return max(0, rule.daily_bytes - self.used(account_id, rule))

    def can_start(self, account_id: str, rule: UsageRule, active: int = 0) -> bool:
        if rule.max_parallel and active >= rule.max_parallel:
            return False
        rem = self.remaining(account_id, rule)
        return rem is None or rem > 0

    def to_dict(self) -> dict:
        return {k: [d, n] for k, (d, n) in self._used.items()}

    @classmethod
    def from_dict(cls, data: dict, clock: Callable[[], float] = time.time) -> "UsageTracker":
        t = cls(clock)
        for k, v in (data or {}).items():
            t._used[str(k)] = (int(v[0]), int(v[1]))
        return t