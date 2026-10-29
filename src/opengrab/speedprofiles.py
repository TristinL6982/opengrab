"""Speed profiles and data caps (M11i).

Named upload/download limit profiles (Normal, Gaming, Night, ...) switchable from the tray
or the scheduler, plus daily/monthly data caps that stop or slow downloads once N bytes are
used. Pure module: time is passed in as a UTC timestamp, nothing here sleeps or does I/O.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

UNLIMITED = 0  # 0 bytes/s means no limit
DAY = "day"
MONTH = "month"
STOP = "stop"
SLOW = "slow"


@dataclass
class SpeedProfile:
    name: str
    download_limit: int = UNLIMITED  # bytes per second, 0 = unlimited
    upload_limit: int = UNLIMITED

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise ValueError("profile name must not be empty")
        self.name = str(self.name).strip()
        if int(self.download_limit) < 0 or int(self.upload_limit) < 0:
            raise ValueError("limits must be >= 0")
        self.download_limit = int(self.download_limit)
        self.upload_limit = int(self.upload_limit)

    def to_dict(self) -> dict:
        return {"name": self.name, "download_limit": self.download_limit,
                "upload_limit": self.upload_limit}

    @classmethod
    def from_dict(cls, data: dict) -> "SpeedProfile":
        return cls(name=data.get("name", ""),
                   download_limit=int(data.get("download_limit", 0) or 0),
                   upload_limit=int(data.get("upload_limit", 0) or 0))


def default_profiles() -> list[SpeedProfile]:
    return [
        SpeedProfile("Normal"),
        SpeedProfile("Gaming", download_limit=512 * 1024, upload_limit=64 * 1024),
        SpeedProfile("Night"),
    ]


class ProfileSet:
    def __init__(self, profiles: list[SpeedProfile] | None = None, active: str = "Normal") -> None:
        self._profiles: dict[str, SpeedProfile] = {}
        for p in (profiles if profiles is not None else default_profiles()):
            self.add(p)
        if not self._profiles:
            self.add(SpeedProfile("Normal"))
        self._active = active if self._key(active) in self._profiles else self.names()[0]
        self._active = self._profiles[self._key(self._active)].name

    @staticmethod
    def _key(name: str) -> str:
        return str(name).strip().lower()

    def names(self) -> list[str]:
        return [p.name for p in self._profiles.values()]

    def add(self, profile: SpeedProfile) -> None:
        self._profiles[self._key(profile.name)] = profile

    def get(self, name: str) -> SpeedProfile | None:
        return self._profiles.get(self._key(name))

    def remove(self, name: str) -> bool:
        key = self._key(name)
        if key not in self._profiles or len(self._profiles) == 1:
            return False
        del self._profiles[key]
        if self._key(self._active) == key:
            self._active = self.names()[0]
        return True

    @property
    def active(self) -> SpeedProfile:
        return self._profiles[self._key(self._active)]

    def switch(self, name: str) -> SpeedProfile:
        p = self.get(name)
        if p is None:
            raise KeyError(name)
        self._active = p.name
        return p

    def to_dict(self) -> dict:
        return {"active": self._active, "profiles": [p.to_dict() for p in self._profiles.values()]}

    @classmethod
    def from_dict(cls, data: dict) -> "ProfileSet":
        profiles = [SpeedProfile.from_dict(d) for d in data.get("profiles", [])]
        return cls(profiles or None, active=data.get("active", "Normal"))


def period_key(ts: float, period: str) -> str:
    t = time.gmtime(ts)
    if period == DAY:
        return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
    if period == MONTH:
        return f"{t.tm_year:04d}-{t.tm_mon:02d}"
    raise ValueError(f"unknown period: {period}")


@dataclass
class DataCap:
    limit_bytes: int
    period: str = DAY
    action: str = STOP
    slow_limit: int = 64 * 1024  # bytes/s once over the cap when action is SLOW
    enabled: bool = True

    def __post_init__(self) -> None:
        if int(self.limit_bytes) <= 0:
            raise ValueError("limit_bytes must be > 0")
        if self.period not in (DAY, MONTH):
            raise ValueError("period must be 'day' or 'month'")
        if self.action not in (STOP, SLOW):
            raise ValueError("action must be 'stop' or 'slow'")
        if int(self.slow_limit) <= 0:
            raise ValueError("slow_limit must be > 0")


@dataclass
class DataCapTracker:
    cap: DataCap
    _key: str = ""
    _used: int = 0
    history: dict = field(default_factory=dict)

    def _roll(self, now: float) -> None:
        key = period_key(now, self.cap.period)
        if key != self._key:
            if self._key:
                self.history[self._key] = self._used
            self._key = key
            self._used = 0

    def add(self, nbytes: int, now: float) -> None:
        if nbytes < 0:
            raise ValueError("nbytes must be >= 0")
        self._roll(now)
        self._used += int(nbytes)

    def used(self, now: float) -> int:
        self._roll(now)
        return self._used

    def remaining(self, now: float) -> int:
        return max(0, self.cap.limit_bytes - self.used(now))

    def exceeded(self, now: float) -> bool:
        return self.cap.enabled and self.used(now) >= self.cap.limit_bytes

    def allows_download(self, now: float) -> bool:
        return not (self.exceeded(now) and self.cap.action == STOP)

    def effective_limit(self, profile_limit: int, now: float) -> int:
        """Download limit (bytes/s, 0 = unlimited) after applying the cap."""
        if not self.exceeded(now) or self.cap.action != SLOW:
            return profile_limit
        if profile_limit == UNLIMITED:
            return self.cap.slow_limit
        return min(profile_limit, self.cap.slow_limit)