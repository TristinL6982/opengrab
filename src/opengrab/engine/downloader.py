"""Resumable, chunked, speed-limited file download.

Design (mirrors JD2's downloader internals, modernized):
  * HEAD (or Range: bytes=0-0) probe  -> total size + accept-ranges
  * if the server accepts ranges:  the remaining byte range is split into
    N chunks (settings.per_link_chunks);  each chunk is fetched by its own
    thread with a Range header and written at its offset into the .part file
  * if not: single streaming append
  * token-bucket speed limit shared by all chunks
  * pause: chunk threads close their stream and wait; on resume each chunk
    re-issues Range from the last byte it wrote  (nothing lost)
  * transient failures (reset/timeout/5xx): chunk-level retry with backoff,
    then link-level retry via the manager (the .part file keeps bytes)
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from ..models import Link, LinkStatus

try:
    from curl_cffi import requests as _crl
    HAVE_CURL_CFFI = True
except Exception:  # pragma: no cover
    _crl = None
    HAVE_CURL_CFFI = False

log = logging.getLogger("opengrab.download")

IMPERSONATE = "chrome"
CHUNK_MIN = 4 * 1024 * 1024        # don't parallelize files < 4 MiB
READ_CHUNK = 256 * 1024            # per-read size for iter_content
PROGRESS_INTERVAL = 0.4            # seconds between on_tick calls


class DownloadError(Exception):
    """Fatal (after retries) or cancelled download."""

    def __init__(self, msg: str, transient: bool = False):
        super().__init__(msg)
        self.transient = transient
        self.cancelled = False


class RangeUnsupported(DownloadError):
    """The server advertised or implied ranges but did not honor them."""


class _RateLimiter:
    """Token bucket shared by all chunk threads of one download."""

    def __init__(self, bytes_per_s: float):
        self.rate = float(bytes_per_s) if bytes_per_s > 0 else 0.0
        self._bal = self.rate      # start with 1s of credit
        self._ts = time.monotonic()
        self._lock = threading.Lock()

    def set_rate(self, bytes_per_s: float) -> None:
        with self._lock:
            new_rate = float(bytes_per_s) if bytes_per_s > 0 else 0.0
            if new_rate == self.rate:
                return
            self.rate = new_rate
            self._bal = min(self._bal, new_rate)
            self._ts = time.monotonic()

    def consume(self, n: int) -> None:
        if self.rate <= 0 or n <= 0:
            return
        remaining = n
        while remaining > 0:
            with self._lock:
                now = time.monotonic()
                self._bal = min(self.rate,
                                self._bal + (now - self._ts) * self.rate)
                self._ts = now
                take = min(remaining, self._bal) if self._bal > 1 else 0.0
                if take > 0:
                    self._bal -= take
            if take <= 0:
                time.sleep(0.02)
                continue
            remaining -= int(take)
            if remaining > 0 and take < 64:
                time.sleep(0.01)


class _Probe:
    size: Optional[int]
    accept_ranges: bool
    final_url: str
    headers: dict

    def __init__(self, size, accept_ranges, final_url, headers):
        self.size = size
        self.accept_ranges = accept_ranges
        self.final_url = final_url
        self.headers = headers or {}


def _open_session(settings) -> object:
    """One curl_cffi session per thread (sessions are not thread-safe)."""
    kw = {"impersonate": IMPERSONATE}
    if getattr(settings, "proxy", ""):
        kw["proxies"] = {"http": settings.proxy, "https": settings.proxy}
    if getattr(settings, "timeout_s", 0):
        kw["timeout"] = float(settings.timeout_s)
    if HAVE_CURL_CFFI:
        return _crl.Session(**kw)
    import httpx
    return httpx.Client(proxy=settings.proxy or None,
                        timeout=settings.timeout_s or 30.0,
                        allow_redirects=True)


def _is_transient(e: Exception) -> bool:
    msg = str(e).lower()
    return any(k in msg for k in (
        "timed out", "timeout", "reset", "refused", "broken pipe",
        "remote end closed", "connection", "temporarily", "503", "502",
        "504", "429", "early hint", "incomplete", "eof"))


def _sanitize(name: str) -> str:
    name = (name or "").strip().replace("\\", "/")
    name = name.split("/")[-1] or "download"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name[:200] or "download"


class LinkDownload:
    """Downloads one link's file.  Run inside a worker thread."""

    def __init__(self, link: Link, url: str, dest_dir: Path,
                 headers: Optional[dict] = None, settings=None,
                 stop_event: Optional[threading.Event] = None,
                 pause_event: Optional[threading.Event] = None,
                 cookies: Optional[dict] = None,
                 rate_limiter: Optional[_RateLimiter] = None,
                 on_tick=None):
        self.link = link
        self.url = url
        self.dest_dir = Path(dest_dir)
        self.headers = dict(headers or {})
        self.settings = settings
        self.stop_event = stop_event or threading.Event()
        self.pause_event = pause_event or threading.Event()
        self.on_tick = on_tick          # callable(self) - update Link
        # cookies captured during grab (session auth) — sent with every
        # download request so resume works on cookie-protected URLs
        self._cookies = dict(cookies or {})

        fname = _sanitize(self.link.filename or
                          os.path.basename(url.split("?")[0]) or "download")
        self.dest = self.dest_dir / fname
        self.part = self.dest_dir / (fname + ".part")
        self.final_path: Optional[Path] = None

        # progress state
        self._file_lock = threading.Lock()   # guards seeks/writes to .part
        self._state_lock = threading.Lock()
        self._total: Optional[int] = None
        self._done = 0
        self._speed = 0.0
        self._samples: list[tuple[float, int]] = []
        self._limiter = rate_limiter or _RateLimiter(0.0)
        self._last_tick = 0.0
        self._active_threads = 0

    # ------------------------------------------------------------------ API
    def pause(self) -> None:
        self.pause_event.set()

    def resume(self) -> None:
        self.pause_event.clear()

    def speed_bps(self) -> float:
        with self._state_lock:
            return self._speed

    def progress(self) -> tuple[Optional[int], int, float]:
        with self._state_lock:
            return self._total, self._done, self._speed

    # ------------------------------------------------------------- internals
    def _stop_check(self) -> None:
        if self.stop_event.is_set():
            raise DownloadError("cancelled", transient=False)

    def _sleep_paused(self) -> None:
        """Block while paused; cancel wins."""
        while self.pause_event.is_set():
            if self.stop_event.is_set():
                raise DownloadError("cancelled", transient=False)
            time.sleep(0.2)

    def _update_speed(self, n: int) -> None:
        now = time.time()
        fire_tick = False
        with self._state_lock:
            self._done += n
            self._samples.append((now, self._done))
            # keep ~5s of history
            while self._samples and now - self._samples[0][0] > 5.0:
                self._samples.pop(0)
            if len(self._samples) >= 2:
                t0, b0 = self._samples[0]
                t1, b1 = self._samples[-1]
                if t1 > t0:
                    self._speed = (b1 - b0) / (t1 - t0)
            if now - self._last_tick > PROGRESS_INTERVAL:
                self._last_tick = now
                fire_tick = True
        # called OUTSIDE the lock: the callback updates the Link and may
        # broadcast/persist — must not hold our state lock while it runs
        if fire_tick and self.on_tick is not None:
            try:
                self.on_tick(self)
            except Exception:
                pass

    def _probe(self) -> _Probe:
        sess = _open_session(self.settings)
        try:
            r = sess.head(self.url, headers=self.headers,
                          timeout=30.0, allow_redirects=True)
            if r.status_code in (405, 403, 501):
                r = sess.get(self.url, headers={**self.headers,
                                                "Range": "bytes=0-0"},
                             stream=True, timeout=30.0, allow_redirects=True)
                r.close()
            size = _int_hdr(r.headers.get("content-length"))
            if size is None:
                # no content-length on HEAD -> ranged GET probe
                r2 = sess.get(self.url, headers={**self.headers,
                                                 "Range": "bytes=0-0"},
                              stream=True, timeout=30.0,
                              allow_redirects=True)
                cr = r2.headers.get("content-range", "")
                m = re.search(r"/(\d+)\s*$", cr)
                size = int(m.group(1)) if m else None
                r2.close()
            ranges = ("bytes" in
                      (r.headers.get("accept-ranges") or "").lower()
                      or "bytes" in
                      (r.headers.get("content-range") or "").lower())
            return _Probe(size, ranges, str(r.url), dict(r.headers))
        finally:
            try:
                sess.close()
            except Exception:
                pass

    def run(self) -> Path:
        """Execute the download; returns final file path. Raises
        DownloadError on failure/cancel."""
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        probe = self._probe()
        self._total = probe.size
        self.url = probe.final_url
        # server-side name wins over plugin guess
        cd = probe.headers.get("content-disposition", "")
        if cd and not self.link.filename:
            m = re.search(r'filename\*?="?([^";]+)"?', cd)
            if m:
                self.link.filename = m.group(1).strip()
                self.dest = self.dest_dir / _sanitize(self.link.filename)
                self.part = self.dest_dir / (self.link.filename + ".part")

        part_size = self.part.stat().st_size if self.part.exists() else 0
        if self._total is not None and 0 < part_size > self._total:
            part_size = 0  # stale/larger part -> start over
        self._done = part_size if part_size <= (self._total or part_size) else 0

        # speed limit: settings value is KB/s (JD2-style)
        limit = (getattr(self.settings, "speed_limit_kbps", 0) or 0) * 1024
        self._limiter.set_rate(limit)

        if self._total is not None and self._done >= self._total:
            self._finalize()
            return self.final_path

        chunks = 1
        if (probe.accept_ranges and self._total
                and self._total - self._done >= CHUNK_MIN):
            chunks = max(1, min(getattr(self.settings, "per_link_chunks", 2) or 1,
                                8))
        try:
            if chunks == 1:
                self._stream(self._done, None)
            else:
                try:
                    self._parallel(self._done, self._total, chunks)
                except RangeUnsupported:
                    # A probe header is only a hint. If a chunk request is
                    # ignored or malformed, discard all partial chunk writes
                    # and restart sequentially to avoid silently corrupting a
                    # file. The server may still accept a normal GET.
                    with self._file_lock:
                        with self.part.open("wb"):
                            pass
                    with self._state_lock:
                        self._done = 0
                        self._samples.clear()
                        self._speed = 0.0
                    self._stream(0, None)
        except DownloadError:
            raise
        except Exception as e:
            if self.stop_event.is_set():
                raise DownloadError("cancelled", transient=False)
            raise DownloadError(f"download failed: {e}",
                                transient=_is_transient(e))
        self._finalize()
        return self.final_path

    # -- single stream -----------------------------------------------------
    def _stream(self, start: int, end: Optional[int]) -> None:
        """(Re)fetch [start, end) appending into the part file."""
        backoff = 1.0
        while True:
            self._stop_check()
            self._sleep_paused()
            sess = _open_session(self.settings)
            try:
                headers = dict(self.headers)
                if self._cookies:
                    headers["Cookie"] = "; ".join(
                        f"{k}={v}" for k, v in self._cookies.items())
                ranged = start > 0 or end is not None
                if ranged:
                    headers["Range"] = f"bytes={start}-"
                    if end is not None:
                        headers["Range"] = f"bytes={start}-{end - 1}"
                r = sess.get(self.url, headers=headers, stream=True,
                             timeout=60.0, allow_redirects=True)
                if ranged:
                    if r.status_code == 200:
                        raise RangeUnsupported("server ignored Range request")
                    if r.status_code != 206:
                        raise DownloadError(
                            f"range request failed: HTTP {r.status_code}",
                            transient=_is_transient(
                                Exception(f"HTTP {r.status_code}")))
                    cr = r.headers.get("content-range", "")
                    match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+)", cr.strip(), re.I)
                    expected_end = (end - 1 if end is not None
                                    else (self._total - 1 if self._total else None))
                    if (not match or int(match.group(1)) != start
                            or (expected_end is not None
                                and int(match.group(2)) != expected_end)
                            or (self._total is not None
                                and int(match.group(3)) != self._total)):
                        raise RangeUnsupported(
                            "server returned an invalid Content-Range")
                elif r.status_code >= 400:
                    raise DownloadError(f"HTTP {r.status_code}",
                                        transient=_is_transient(
                                            Exception(f"HTTP {r.status_code}")))
                # unknown-size probe: learn total now
                if self._total is None:
                    cr = r.headers.get("content-range", "")
                    m = re.search(r"/(\d+)\s*$", cr)
                    if m:
                        self._total = int(m.group(1))
                    elif _int_hdr(r.headers.get("content-length")) is not None:
                        self._total = start + int(r.headers["content-length"])
                expected_bytes = (end - start if ranged and end is not None
                                  else (self._total - start if ranged
                                        and self._total is not None else None))
                received = 0
                mode = "r+b" if self.part.exists() else "wb"
                with open(self.part, mode) as f:
                    if start > 0:
                        f.seek(start)
                    for piece in r.iter_content(READ_CHUNK):
                        if not piece:
                            continue
                        self._stop_check()
                        self._sleep_paused()
                        self._limiter.consume(len(piece))
                        f.write(piece)
                        received += len(piece)
                        self._update_speed(len(piece))
                if expected_bytes is not None and received != expected_bytes:
                    raise DownloadError(
                        f"range ended early: received {received} of {expected_bytes} bytes",
                        transient=True)
                return
            except DownloadError:
                raise
            except Exception as e:
                if self.stop_event.is_set():
                    raise DownloadError("cancelled", transient=False)
                backoff = min(backoff * 2, 30.0)
                log.warning("stream %s interrupted (%s) - retrying in %.0fs",
                            self.url, e, backoff)
                time.sleep(backoff)
            finally:
                try:
                    sess.close()
                except Exception:
                    pass

    # -- parallel chunks ------------------------------------------------------
    def _parallel(self, start: int, total: int, n: int) -> None:
        # Prepare the file before workers open independent handles at offsets.
        # Holding one lock around an entire stream would serialize the chunks.
        self.part.parent.mkdir(parents=True, exist_ok=True)
        with self._file_lock:
            mode = "r+b" if self.part.exists() else "w+b"
            with open(self.part, mode) as f:
                f.truncate(total)
        span = total - start
        size = span // n
        ranges = []
        for i in range(n):
            s = start + i * size
            e = total if i == n - 1 else start + (i + 1) * size
            ranges.append((s, e))
        errors: list[Exception] = []
        err_lock = threading.Lock()
        threads = []

        def worker(rng: tuple[int, int]) -> None:
            try:
                self._stream(*rng)
            except DownloadError as e:
                if not e.cancelled and not self.stop_event.is_set():
                    with err_lock:
                        errors.append(e)
            except Exception as e:
                with err_lock:
                    errors.append(e)

        for rng in ranges:
            t = threading.Thread(target=worker, args=(rng,), daemon=True,
                                 name=f"dl-{self.link.id[:8]}")
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        if any(isinstance(error, RangeUnsupported) for error in errors):
            raise RangeUnsupported(str(next(e for e in errors
                                           if isinstance(e, RangeUnsupported))))
        if errors and self.stop_event.is_set():
            raise DownloadError("cancelled", transient=False)
        if errors:
            raise DownloadError(f"chunk failed: {errors[0]}",
                                transient=_is_transient(errors[0]))

    # -- finalize -------------------------------------------------------------
    def _finalize(self) -> None:
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        final = self.dest
        i = 1
        while final.exists():
            stem, suf = self.dest.stem, self.dest.suffix
            final = self.dest_dir / f"{stem}_{i}{suf}"
            i += 1
        if self.part.exists():
            os.replace(self.part, final)
        self.final_path = final
        with self._state_lock:
            self._speed = 0.0
            if self._total is None:
                self._total = final.stat().st_size
                self._done = self._total


def _int_hdr(v: Optional[str]) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
