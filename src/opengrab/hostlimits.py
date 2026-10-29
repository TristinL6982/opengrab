"""Per-host connection limits (M11b): cap how many downloads run at once per host."""
from __future__ import annotations

from urllib.parse import urlsplit


def _norm(host: str) -> str:
    host = host.strip().lower()
    return host[4:] if host.startswith("www.") else host


def host_of(url: str) -> str:
    """Lower-case host of url without a leading 'www.' ('' if none)."""
    return _norm(urlsplit(url).hostname or "")


class HostLimits:
    """Tracks active downloads per host. A limit of 0 means unlimited."""

    def __init__(self, default_max: int = 0, per_host: dict | None = None):
        if int(default_max) < 0:
            raise ValueError("default_max must be >= 0")
        self.default_max = int(default_max)
        self.per_host: dict[str, int] = {}
        self._active: dict[str, int] = {}
        for host, n in (per_host or {}).items():
            self.set_limit(host, n)

    def set_limit(self, host: str, n: int) -> None:
        n = int(n)
        if n < 0:
            raise ValueError("limit must be >= 0")
        self.per_host[_norm(host)] = n

    def limit_for(self, url: str) -> int:
        return self.per_host.get(host_of(url), self.default_max)

    def active(self, url: str) -> int:
        return self._active.get(host_of(url), 0)

    def can_start(self, url: str) -> bool:
        lim = self.limit_for(url)
        return lim == 0 or self.active(url) < lim

    def start(self, url: str) -> bool:
        """Count one more active download for url's host; False (and no change) if at the limit."""
        if not self.can_start(url):
            return False
        h = host_of(url)
        self._active[h] = self._active.get(h, 0) + 1
        return True

    def finish(self, url: str) -> None:
        """Count one download for url's host as done; never goes below zero."""
        h = host_of(url)
        n = self._active.get(h, 0)
        if n <= 1:
            self._active.pop(h, None)
        else:
            self._active[h] = n - 1