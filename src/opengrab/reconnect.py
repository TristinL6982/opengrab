"""Reconnect (M11d): decide when to reset the router/IP and run it through injected actions."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ReconnectConfig:
    enabled: bool = False
    min_wait_seconds: int = 300
    cooldown_seconds: int = 600
    max_attempts: int = 3


def _int_field(d: dict, key: str, default: int, minimum: int) -> int:
    value = d.get(key, default)
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bad {key}") from exc
    if value < minimum:
        raise ValueError(f"bad {key}")
    return value


def config_from_dict(d: dict | None) -> ReconnectConfig:
    d = d or {}
    return ReconnectConfig(
        enabled=bool(d.get("enabled", False)),
        min_wait_seconds=_int_field(d, "min_wait_seconds", 300, 0),
        cooldown_seconds=_int_field(d, "cooldown_seconds", 600, 0),
        max_attempts=_int_field(d, "max_attempts", 3, 1),
    )


@dataclass(frozen=True)
class ReconnectResult:
    ok: bool
    old_ip: str
    new_ip: str
    attempts: int
    error: str = ""


class Reconnector:
    """Runs do_reconnect() until get_ip() reports a new address, at most max_attempts times."""

    def __init__(self, config: ReconnectConfig, do_reconnect: Callable[[], None],
                 get_ip: Callable[[], str], clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.do_reconnect = do_reconnect
        self.get_ip = get_ip
        self.clock = clock
        self._last: float | None = None
        self.history: list[ReconnectResult] = []

    def _ip(self) -> str:
        try:
            return (self.get_ip() or "").strip()
        except Exception:  # noqa: BLE001 - an IP lookup failure means "unknown"
            return ""

    def next_allowed_at(self) -> float | None:
        if self._last is None:
            return None
        return self._last + self.config.cooldown_seconds

    def should_reconnect(self, wait_seconds: float, active_downloads: int = 0) -> bool:
        cfg = self.config
        if not cfg.enabled or wait_seconds < cfg.min_wait_seconds or active_downloads > 0:
            return False
        allowed = self.next_allowed_at()
        return allowed is None or self.clock() >= allowed

    def reconnect(self) -> ReconnectResult:
        old = self._ip()
        new = old
        error = ""
        attempts = 0
        for attempts in range(1, self.config.max_attempts + 1):
            try:
                self.do_reconnect()
            except Exception as exc:  # noqa: BLE001 - any router/script failure is a failed attempt
                error = f"reconnect failed: {exc}"
                continue
            new = self._ip()
            if new and new != old:
                error = ""
                break
            error = "ip unchanged"
        self._last = self.clock()
        result = ReconnectResult(error == "", old, new, attempts, error)
        self.history.append(result)
        return result