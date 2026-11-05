"""Offline IP-to-country lookup for the torrent peers view.

Privacy: a peer's address is never sent to any lookup service. The country
comes from a local copy of the DB-IP "IP to Country Lite" CSV (CC BY 4.0,
attribution "IP geolocation by DB-IP"), an optional download kept in
``<data dir>/addons/geoip/``. Rows are ``start,end,country`` with IPv4 and
IPv6 ranges; they are loaded lazily into sorted integer arrays and searched
with bisect. Nothing is downloaded unless the user presses Install.
"""
from __future__ import annotations

import bisect
import csv
import gzip
import ipaddress
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("opengrab.geoip")

ATTRIBUTION = "IP geolocation by DB-IP"
ATTRIBUTION_URL = "https://db-ip.com"
FILE_NAME = "dbip-country-lite.csv"
URL_TEMPLATE = "https://download.db-ip.com/free/dbip-country-lite-{year:04d}-{month:02d}.csv.gz"

_lock = threading.Lock()
_job: dict = {}
_cache: dict = {}   # path -> (mtime, CountryDB)


def db_dir(data_dir: Optional[Path] = None) -> Path:
    from .addons import addons_root
    return addons_root(data_dir) / "geoip"


def db_path(data_dir: Optional[Path] = None) -> Path:
    return db_dir(data_dir) / FILE_NAME


def is_installed(data_dir: Optional[Path] = None) -> bool:
    return db_path(data_dir).is_file()


class CountryDB:
    """Sorted integer ranges per IP family; ``lookup`` is O(log n)."""

    def __init__(self):
        self._v4: tuple[list, list, list] = ([], [], [])
        self._v6: tuple[list, list, list] = ([], [], [])

    @classmethod
    def load(cls, path: Path) -> "CountryDB":
        db = cls()
        v4, v6 = ([], [], []), ([], [], [])
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            for row in csv.reader(fh):
                if len(row) < 3:
                    continue
                try:
                    a, b = ipaddress.ip_address(row[0].strip()), ipaddress.ip_address(row[1].strip())
                except ValueError:
                    continue  # header or damaged row
                cc = row[2].strip().upper()
                if len(cc) != 2 or a.version != b.version:
                    continue
                tgt = v4 if a.version == 4 else v6
                tgt[0].append(int(a))
                tgt[1].append(int(b))
                tgt[2].append(cc)
        for tgt in (v4, v6):
            order = sorted(range(len(tgt[0])), key=tgt[0].__getitem__)
            for i, col in enumerate(tgt):
                tgt[i][:] = [col[j] for j in order]
        db._v4, db._v6 = v4, v6
        return db

    def __len__(self) -> int:
        return len(self._v4[0]) + len(self._v6[0])

    def lookup(self, ip: str) -> str:
        """Two-letter country code, or "" when unknown, private or invalid."""
        try:
            addr = ipaddress.ip_address(str(ip).strip().strip("[]").split("%")[0])
        except ValueError:
            return ""
        if addr.version == 6 and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        starts, ends, codes = self._v4 if addr.version == 4 else self._v6
        n = int(addr)
        i = bisect.bisect_right(starts, n) - 1
        if i >= 0 and n <= ends[i]:
            cc = codes[i]
            return "" if cc in ("ZZ", "XX") else cc
        return ""


def load_db(data_dir: Optional[Path] = None) -> Optional[CountryDB]:
    """The cached database, or None when it is not installed."""
    path = db_path(data_dir)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        db = CountryDB.load(path)
    except OSError:
        return None
    with _lock:
        _cache[key] = (mtime, db)
    return db


def flag(cc: str) -> str:
    """Flag emoji from two regional indicator letters ("" for anything else)."""
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isascii() or not cc.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in cc)


def summarize(codes: list[str]) -> list[dict]:
    """Per-country peer counts, largest first. Unknown peers are skipped."""
    counts: dict[str, int] = {}
    for cc in codes:
        if cc:
            counts[cc] = counts.get(cc, 0) + 1
    return [{"country": cc, "flag": flag(cc), "count": n}
            for cc, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def status(data_dir: Optional[Path] = None) -> dict:
    p = db_path(data_dir)
    with _lock:
        job = dict(_job) or None
    installed = p.is_file()
    return {"installed": installed, "attribution": ATTRIBUTION, "attribution_url": ATTRIBUTION_URL,
            "disk_bytes": p.stat().st_size if installed else 0, "job": job}


def _months(now: Optional[float] = None, count: int = 3) -> list[tuple[int, int]]:
    t = time.gmtime(now if now is not None else time.time())
    y, m = t.tm_year, t.tm_mon
    out = []
    for _ in range(count):  # the file for the current month may not be published yet
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def install(data_dir: Optional[Path] = None, fetch: Optional[Callable[[str], bytes]] = None,
            now: Optional[float] = None) -> dict:
    """Download the newest monthly file (gzip CSV). ``fetch(url) -> bytes`` is for tests."""
    target = db_dir(data_dir)
    target.mkdir(parents=True, exist_ok=True)
    last_err = "no database found"
    for year, month in _months(now):
        url = URL_TEMPLATE.format(year=year, month=month)
        try:
            if fetch is not None:
                raw = fetch(url)
            else:
                from .tls import urlopen
                req = urllib.request.Request(url, headers={"User-Agent": "OpenGrab geoip installer"})
                with urlopen(req, timeout=60) as resp:
                    raw = resp.read()
        except Exception as exc:  # 404 for a month not yet published: try the previous one
            last_err = str(exc)[:200]
            continue
        try:
            text = gzip.decompress(raw)
        except OSError:
            text = raw
        tmp = target / (FILE_NAME + ".part")
        tmp.write_bytes(text)
        probe = CountryDB.load(tmp)
        if not len(probe):
            tmp.unlink(missing_ok=True)
            last_err = "the downloaded file has no usable rows"
            continue
        os.replace(tmp, db_path(data_dir))
        return status(data_dir)
    raise RuntimeError("could not download the country database: " + last_err)


def install_async(data_dir: Optional[Path] = None, on_done: Optional[Callable] = None) -> dict:
    with _lock:
        if _job.get("running"):
            return dict(_job)
        _job.clear()
        _job.update(running=True, error="")

    def run():
        try:
            install(data_dir)
            ok, err = True, ""
        except Exception as exc:
            log.warning("country database install failed: %s", exc)
            ok, err = False, str(exc)[:300]
        with _lock:
            _job.update(running=False, ok=ok, error=err)
        if on_done:
            try:
                on_done(ok, err)
            except Exception:
                pass

    threading.Thread(target=run, name="geoip-install", daemon=True).start()
    with _lock:
        return dict(_job)


def remove(data_dir: Optional[Path] = None) -> bool:
    p = db_path(data_dir)
    with _lock:
        _cache.pop(str(p), None)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
