"""BitTorrent engine wrapper (M11o).

Pure planning layer over an injected libtorrent-like session: magnet parsing,
per-file selection, sequential mode, DHT/PEX/LSD settings and seeding limits.
libtorrent itself is optional and never imported here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

SKIP = 0
NORMAL = 4
HIGH = 7


@dataclass
class Magnet:
    info_hash: str
    name: str = ""
    trackers: list = field(default_factory=list)


def parse_magnet(uri: str) -> Magnet:
    text = (uri or "").strip()
    parsed = urlparse(text)
    if parsed.scheme.lower() != "magnet":
        raise ValueError("not a magnet link")
    query = parse_qs(parsed.query)
    info_hash = ""
    for xt in query.get("xt", []):
        if xt.lower().startswith("urn:btih:"):
            info_hash = xt[9:].strip().lower()
            break
    if len(info_hash) not in (32, 40):
        raise ValueError("magnet link has no valid btih hash")
    name = unquote(query.get("dn", [""])[0])
    trackers = []
    for tr in query.get("tr", []):
        tr = tr.strip()
        if tr and tr not in trackers:
            trackers.append(tr)
    return Magnet(info_hash, name, trackers)


@dataclass
class TorrentSettings:
    dht: bool = True
    pex: bool = True
    lsd: bool = True
    sequential: bool = False
    ratio_limit: float = 0.0
    seed_minutes: int = 0

    def session_dict(self) -> dict:
        return {"enable_dht": self.dht, "enable_lsd": self.lsd, "enable_pex": self.pex}


def settings_from_dict(data) -> TorrentSettings:
    data = data if isinstance(data, dict) else {}
    s = TorrentSettings()
    for key in ("dht", "pex", "lsd", "sequential"):
        if key in data:
            setattr(s, key, bool(data[key]))
    try:
        s.ratio_limit = max(0.0, float(data.get("ratio_limit", 0.0)))
    except (TypeError, ValueError):
        s.ratio_limit = 0.0
    try:
        s.seed_minutes = max(0, int(data.get("seed_minutes", 0)))
    except (TypeError, ValueError):
        s.seed_minutes = 0
    return s


def file_priorities(file_count: int, selected=None, high=None) -> list:
    """One priority per file: SKIP for unselected, HIGH for high, NORMAL otherwise."""
    count = max(0, int(file_count))
    chosen = set(range(count)) if selected is None else {i for i in selected if 0 <= i < count}
    boosted = set(high or [])
    out = []
    for index in range(count):
        if index not in chosen:
            out.append(SKIP)
        elif index in boosted:
            out.append(HIGH)
        else:
            out.append(NORMAL)
    return out


def should_stop_seeding(settings: TorrentSettings, uploaded: int, downloaded: int, seeded_seconds: float) -> bool:
    if settings.ratio_limit > 0 and downloaded > 0 and uploaded / downloaded >= settings.ratio_limit:
        return True
    if settings.seed_minutes > 0 and seeded_seconds >= settings.seed_minutes * 60:
        return True
    return False


class TorrentEngine:
    """Thin wrapper over an injected session object (add/remove/apply settings)."""

    def __init__(self, session, settings: TorrentSettings | None = None):
        self.session = session
        self.settings = settings or TorrentSettings()
        self.handles = {}
        self.session.apply_settings(self.settings.session_dict())

    def add_magnet(self, uri: str, save_path: str, extra_trackers=None):
        magnet = parse_magnet(uri)
        if magnet.info_hash in self.handles:
            return self.handles[magnet.info_hash]
        trackers = list(magnet.trackers)
        for tr in extra_trackers or []:
            if tr not in trackers:
                trackers.append(tr)
        handle = self.session.add(
            {
                "info_hash": magnet.info_hash,
                "name": magnet.name,
                "save_path": save_path,
                "trackers": trackers,
                "sequential": self.settings.sequential,
            }
        )
        self.handles[magnet.info_hash] = handle
        return handle

    def select_files(self, info_hash: str, file_count: int, selected=None, high=None) -> list:
        priorities = file_priorities(file_count, selected, high)
        self.handles[info_hash].set_priorities(priorities)
        return priorities

    def check_seeding(self, info_hash: str, uploaded: int, downloaded: int, seeded_seconds: float) -> bool:
        if info_hash in self.handles and should_stop_seeding(self.settings, uploaded, downloaded, seeded_seconds):
            self.remove(info_hash)
            return True
        return False

    def remove(self, info_hash: str) -> bool:
        handle = self.handles.pop(info_hash, None)
        if handle is None:
            return False
        self.session.remove(handle)
        return True