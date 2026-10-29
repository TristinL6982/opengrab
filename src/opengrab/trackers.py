"""Torrent tracker management (M11j).

Built-in tracker list with updates from public lists (text, one URL per line), per-torrent
add/remove, and health tracking from announce results (successes, consecutive failures,
seeds/peers). Pure module: no network, no sleeping; time is passed in as a timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

SCHEMES = ("udp", "http", "https", "wss")

DEFAULT_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
]


def normalize_tracker(url: str) -> str | None:
    """Return a cleaned tracker URL, or None when it isn't a usable tracker URL."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in SCHEMES or not parts.hostname:
        return None
    host = parts.hostname.lower()
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path, parts.query, ""))


def parse_tracker_list(text: str) -> list[str]:
    """Parse a public tracker list: one URL per line, blank lines and # comments ignored.

    Invalid lines are skipped and duplicates removed, keeping the first occurrence.
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url = normalize_tracker(line)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


@dataclass
class TrackerHealth:
    url: str
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    seeds: int = 0
    peers: int = 0
    last_error: str = ""
    last_announce: float = 0.0

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "seeds": self.seeds,
            "peers": self.peers,
            "last_error": self.last_error,
            "last_announce": self.last_announce,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrackerHealth":
        return cls(
            url=str(data["url"]),
            successes=int(data.get("successes", 0)),
            failures=int(data.get("failures", 0)),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            seeds=int(data.get("seeds", 0)),
            peers=int(data.get("peers", 0)),
            last_error=str(data.get("last_error", "")),
            last_announce=float(data.get("last_announce", 0.0)),
        )


class TrackerManager:
    """Keeps the global tracker list and per-tracker health."""

    def __init__(self, trackers: list[str] | None = None, max_failures: int = 3,
                 update_interval: float = 86400.0) -> None:
        self.max_failures = max(1, int(max_failures))
        self.update_interval = float(update_interval)
        self.last_update = 0.0
        self._urls: list[str] = []
        self._health: dict[str, TrackerHealth] = {}
        for url in (DEFAULT_TRACKERS if trackers is None else trackers):
            self.add(url)

    def urls(self) -> list[str]:
        return list(self._urls)

    def add(self, url: str) -> bool:
        norm = normalize_tracker(url)
        if norm is None or norm in self._urls:
            return False
        self._urls.append(norm)
        self._health[norm] = TrackerHealth(norm)
        return True

    def remove(self, url: str) -> bool:
        norm = normalize_tracker(url)
        if norm is None or norm not in self._urls:
            return False
        self._urls.remove(norm)
        self._health.pop(norm, None)
        return True

    def merge(self, text: str, now: float = 0.0) -> int:
        """Add every new tracker from a public list; return how many were added."""
        added = sum(1 for url in parse_tracker_list(text) if self.add(url))
        self.last_update = float(now)
        return added

    def needs_update(self, now: float) -> bool:
        return self.last_update <= 0 or now - self.last_update >= self.update_interval

    def health(self, url: str) -> TrackerHealth | None:
        norm = normalize_tracker(url)
        return self._health.get(norm) if norm else None

    def record_announce(self, url: str, ok: bool, seeds: int = 0, peers: int = 0,
                        error: str = "", now: float = 0.0) -> None:
        h = self.health(url)
        if h is None:
            return
        h.last_announce = float(now)
        if ok:
            h.successes += 1
            h.consecutive_failures = 0
            h.seeds = max(0, int(seeds))
            h.peers = max(0, int(peers))
            h.last_error = ""
        else:
            h.failures += 1
            h.consecutive_failures += 1
            h.last_error = str(error)

    def is_healthy(self, url: str) -> bool:
        h = self.health(url)
        return h is not None and h.consecutive_failures < self.max_failures

    def healthy_urls(self) -> list[str]:
        return [u for u in self._urls if self.is_healthy(u)]

    def for_torrent(self, own: list[str] | None = None, extra: list[str] | None = None,
                    removed: list[str] | None = None) -> list[str]:
        """Tracker list for one torrent: its own trackers, then extras, then healthy globals,
        minus anything the user removed for that torrent. Deduplicated, order kept."""
        drop = {n for n in (normalize_tracker(u) for u in (removed or [])) if n}
        out: list[str] = []
        for url in list(own or []) + list(extra or []) + self.healthy_urls():
            norm = normalize_tracker(url)
            if norm and norm not in drop and norm not in out:
                out.append(norm)
        return out

    def to_dict(self) -> dict:
        return {
            "max_failures": self.max_failures,
            "update_interval": self.update_interval,
            "last_update": self.last_update,
            "trackers": [self._health[u].to_dict() for u in self._urls],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrackerManager":
        mgr = cls(trackers=[], max_failures=int(data.get("max_failures", 3)),
                  update_interval=float(data.get("update_interval", 86400.0)))
        mgr.last_update = float(data.get("last_update", 0.0))
        for item in data.get("trackers", []):
            h = TrackerHealth.from_dict(item)
            if mgr.add(h.url):
                mgr._health[normalize_tracker(h.url)] = h
        return mgr