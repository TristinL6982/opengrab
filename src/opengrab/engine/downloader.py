"""Resumable, chunked, speed-limited file download.

Design (mirrors JD2's downloader internals, modernized):
  * HEAD (or Range: bytes=0-0) probe  -> total size + accept-ranges
  * if the server accepts ranges:  the remaining byte range is split into
    up to 16 segments (settings.per_link_chunks, W6 dynamic);  each is fetched by its own
    thread with a Range header and written at its offset into the .part file
  * if not: single streaming append
  * token-bucket speed limit shared by all chunks
  * pause: chunk threads close their stream and wait; on resume each chunk
    re-issues Range from the last byte it wrote  (nothing lost)
  * transient failures (reset/timeout/5xx): chunk-level retry with backoff,
    then link-level retry via the manager (the .part file keeps bytes)
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
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


def _raise_for_challenge(url: str, r) -> None:
    """W2: a Cloudflare/DDoS-Guard/captcha page instead of the file."""
    from ..challenge import CHALLENGE_STATUSES, raise_for_challenge
    if r.status_code not in CHALLENGE_STATUSES:
        return
    ctype = str(r.headers.get("content-type") or "").lower()
    if "html" not in ctype and "cf-mitigated" not in {k.lower() for k in r.headers}:
        return
    body = b""
    try:
        for chunk in r.iter_content(chunk_size=16384):
            body += chunk
            if len(body) >= 200000:
                break
    except Exception:
        pass
    raise_for_challenge(str(getattr(r, "url", "") or url), r.status_code,
                        dict(r.headers), body)


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


def _cd_filename(header: str) -> Optional[str]:
    if not header:
        return None
    
    # Prefer RFC 5987 filename*=UTF-8''<percent-encoded>
    m = re.search(r'filename\*=(?:UTF-8\'\'|utf-8\'\')([^;\s]+)', header, re.IGNORECASE)
    if m:
        try:
            return _sanitize(urllib.parse.unquote(m.group(1), encoding='utf-8'))
        except Exception:
            return None
    
    # Else look for filename="..." or filename=token
    m = re.search(r'filename="?([^";]+)"?', header, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        # Take just the basename and sanitize it
        return _sanitize(os.path.basename(name))
    
    return None


def _cancelled():
    e = DownloadError("cancelled", transient=False)
    e.cancelled = True
    return e


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
        # a shared limiter is owned (and rated) by the manager's flow control
        self._own_limiter = rate_limiter is None
        self._last_tick = 0.0
        self._active_threads = 0

        # Sidecar chunk map
        self.meta = str(self.part) + ".json"
        self._chunks = None
        self._meta_lock = threading.Lock()
        self._abort = threading.Event()

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
            raise _cancelled()
        if self._abort.is_set():
            raise _cancelled()

    def _sleep_paused(self) -> None:
        """Block while paused; cancel wins."""
        while self.pause_event.is_set():
            if self.stop_event.is_set():
                raise _cancelled()
            if self._abort.is_set():
                raise _cancelled()
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
        headers = dict(self.headers)
        if self._cookies:  # the probe must carry the same cookies as the transfer
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        try:
            r = sess.head(self.url, headers=headers,
                          timeout=30.0, allow_redirects=True)
            if r.status_code in (405, 403, 501, 429, 503):
                r = sess.get(self.url, headers={**headers,
                                                "Range": "bytes=0-0"},
                             stream=True, timeout=30.0, allow_redirects=True)
                try:
                    _raise_for_challenge(self.url, r)
                finally:
                    r.close()
            size = _int_hdr(r.headers.get("content-length"))
            if size is None:
                # no content-length on HEAD -> ranged GET probe
                r2 = sess.get(self.url, headers={**headers,
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

    def _save_meta(self) -> None:
        self._sync_chunks()
        if self._chunks is None:
            return
        # Chunk threads checkpoint concurrently: serialize the snapshot,
        # the shared temp file and the atomic replace.
        with self._meta_lock:
            with self._state_lock:
                data = {"url": self.url, "total": self._total,
                        "chunks": [list(c) for c in self._chunks]}
                v = getattr(self, "_validators", None)
                if v is not None:  # W3: identify the remote file version
                    data["validators"] = {"etag": v.etag, "last_modified": v.last_modified,
                                          "length": v.length}
            tmp_path = self.meta + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.meta)

    def _load_meta(self) -> list[list[int]]:
        if not os.path.exists(self.meta):
            return None
        try:
            with open(self.meta, "r") as f:
                data = json.load(f)
            if (data.get("total") != self._total or
                    not isinstance(data.get("chunks"), list)):
                return None
            saved = data.get("validators")
            current = getattr(self, "_validators", None)
            if isinstance(saved, dict) and current is not None:
                from ..recovery import Validators, can_resume
                old = Validators(str(saved.get("etag") or ""), str(saved.get("last_modified") or ""),
                                 saved.get("length"))
                if (old.etag or old.last_modified) and (current.etag or current.last_modified)                         and not can_resume(old, current):
                    # W3: the server has a different file now; the part is useless
                    self.link.extra["note"] = "restarted: the file changed on the server"
                    return None
            chunks = []
            for ch in data["chunks"]:
                if (not isinstance(ch, list) or len(ch) != 3 or
                        any(not isinstance(x, int) for x in ch) or
                        ch[0] < 0 or ch[2] < 0 or ch[2] > ch[1] - ch[0]):
                    return None
                chunks.append(ch)
            return chunks
        except Exception:
            return None

    def _drop_meta(self) -> None:
        with self._meta_lock:
            try:
                os.remove(self.meta)
            except Exception:
                pass

    def run(self) -> Path:
        """Execute the download; returns final file path. Raises
        DownloadError on failure/cancel."""
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        probe = self._probe()
        self._total = probe.size
        self.url = probe.final_url
        from ..recovery import validators_from_headers
        self._validators = validators_from_headers(probe.headers)
        if self._validators.length is None and self._total is not None:
            self._validators.length = self._total
        # server-side name wins over plugin guess
        cd = probe.headers.get("content-disposition", "")
        name = _cd_filename(cd)
        if name and not self.link.filename:
            self.link.filename = name
            self.dest = self.dest_dir / name
            self.part = self.dest_dir / (name + ".part")
            self.meta = str(self.part) + ".json"

        # speed limit: settings value is KB/s (JD2-style)
        if self._own_limiter:
            limit = (getattr(self.settings, "speed_limit_kbps", 0) or 0) * 1024
            self._limiter.set_rate(limit)

        chunks_meta = self._load_meta() if self._total else None
        try:
            if chunks_meta is not None:
                # Resume a multi-chunk download from its sidecar map. The
                # .part size is meaningless here (it was pre-allocated).
                self._chunks = chunks_meta
                self._planner = self._new_planner(self._total, chunks_meta)
                with self._state_lock:
                    self._done = sum(ch[2] for ch in chunks_meta)
                    self.start_done = self._done
                self._parallel(self._total, self._chunks)
            else:
                if os.path.exists(self.meta):
                    # Invalid or stale map: the .part cannot be trusted.
                    self._drop_meta()
                    if self.part.exists():
                        self.part.unlink()
                part_size = self.part.stat().st_size if self.part.exists() else 0
                if self._total is not None and part_size > self._total:
                    self.part.unlink()
                    part_size = 0
                with self._state_lock:
                    self._done = part_size
                    self.start_done = part_size
                if self._total is not None and part_size == self._total:
                    pass  # complete single-stream part: finalize below
                elif (probe.accept_ranges and self._total and part_size == 0
                        and self._total >= CHUNK_MIN
                        and (getattr(self.settings, "per_link_chunks", 2) or 1) > 1):
                    self._planner = self._new_planner(self._total, None)
                    self._sync_chunks()
                    self._save_meta()
                    try:
                        self._parallel(self._total, self._chunks)
                    except RangeUnsupported:
                        # A probe header is only a hint: restart sequentially.
                        self._drop_meta()
                        self._chunks = None
                        self._planner = None
                        self._abort.clear()  # siblings aborted; start clean
                        with self._file_lock:
                            with self.part.open("wb"):
                                pass
                        with self._state_lock:
                            self._done = 0
                            self._samples.clear()
                            self._speed = 0.0
                        self._stream(0, None)
                else:
                    self._abort.clear()
                    self._stream(part_size, None)
        except DownloadError:
            raise
        except Exception as e:
            if self.stop_event.is_set():
                raise _cancelled()
            raise DownloadError(f"download failed: {e}",
                                transient=_is_transient(e))
        if (self._total is not None and self.part.exists()
                and self.part.stat().st_size != self._total):
            raise DownloadError("size mismatch", transient=True)
        self._finalize()
        self._drop_meta()
        return self.final_path

    # -- single stream -----------------------------------------------------
    def _stream(self, start: int, end: Optional[int], idx: int = None, seg=None) -> None:
        """(Re)fetch [start, end) appending into the part file.

        With ``seg`` (a segments.Segment) the range is live: another worker may
        split it, shrinking ``seg.end``, and this stream then stops early.
        """
        backoff = 1.0
        attempts = 0
        
        # The caller passes the real resume offset; pos tracks every byte
        # written so retries continue from where the data actually ends.
        pos = start
        track = seg is not None

        def seg_end() -> int:
            with self._state_lock:
                return seg.end + 1

        while True:
            self._stop_check()
            self._sleep_paused()
            
            if track:
                end = seg_end()
                if pos >= end:
                    return  # the remaining range was handed to another worker
            sess = _open_session(self.settings)
            try:
                headers = dict(self.headers)
                if self._cookies:
                    headers["Cookie"] = "; ".join(
                        f"{k}={v}" for k, v in self._cookies.items())
                    
                ranged = pos > 0 or end is not None
                if ranged:
                    headers["Range"] = f"bytes={pos}-"
                    if end is not None:
                        headers["Range"] = f"bytes={pos}-{end - 1}"
                        
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
                    
                    # Validate content range against what we expect
                    expected_end = (end - 1 if end is not None
                                    else (self._total - 1 if self._total else None))
                    
                    if (not match or int(match.group(1)) != pos
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
                        self._total = pos + int(r.headers["content-length"])
                        
                expected_bytes = (end - pos if ranged and end is not None
                                  else (self._total - pos if ranged
                                        and self._total is not None else None))
                                  
                received = 0
                
                mode = "r+b" if self.part.exists() else "wb"
                with open(self.part, mode) as f:
                    if pos > 0:
                        f.seek(pos)
                    last_save = time.monotonic()

                    seg_mark = (time.monotonic(), pos)

                    def checkpoint() -> None:
                        # The sidecar may only claim bytes that are on disk.
                        f.flush()
                        os.fsync(f.fileno())
                        if track:
                            self._sync_chunks()
                            self._save_meta()

                    shrunk = False
                    try:
                        for piece in r.iter_content(READ_CHUNK):
                            if not piece:
                                continue
                            self._stop_check()
                            self._sleep_paused()
                            if track:
                                limit = seg_end()
                                if pos >= limit:
                                    shrunk = True
                                    break
                                if pos + len(piece) > limit:
                                    piece = piece[:limit - pos]
                            self._limiter.consume(len(piece))
                            f.write(piece)
                            pos += len(piece)
                            received += len(piece)
                            self._update_speed(len(piece))
                            now = time.monotonic()
                            if track:
                                with self._state_lock:
                                    seg.done = max(seg.done, min(pos - seg.start, seg.length))
                                    if now - seg_mark[0] >= 0.5:
                                        seg.speed = (pos - seg_mark[1]) / (now - seg_mark[0])
                                        seg_mark = (now, pos)
                            if track and now - last_save >= 1.0:
                                checkpoint()
                                last_save = now
                    finally:
                        checkpoint()

                if track:
                    if shrunk or pos >= seg_end():
                        return
                    raise DownloadError(
                        f"range ended early: stopped at {pos} of {seg_end()}", transient=True)
                if expected_bytes is not None and received != expected_bytes:
                    raise DownloadError(
                        f"range ended early: received {received} of {expected_bytes} bytes",
                        transient=True)
                return
                
            except DownloadError:
                raise
            except Exception as e:
                attempts += 1
                if self.stop_event.is_set():
                    raise _cancelled()
                
                if attempts > max(1, getattr(self.settings, 'retry_max', 2)):
                    raise DownloadError(f"download failed after {attempts} attempts: {e}", 
                                       transient=True)
                    
                backoff = min(backoff * 2, 30.0)
                log.warning("stream %s interrupted (%s) - retrying in %.0fs",
                            self.url, e, backoff)
                # Interruptible backoff: stop/abort must not wait it out.
                deadline = time.monotonic() + backoff
                while time.monotonic() < deadline:
                    self._stop_check()
                    self.stop_event.wait(min(0.25, max(0.0, deadline - time.monotonic())))
            finally:
                try:
                    sess.close()
                except Exception:
                    pass

    # -- parallel segments (W6: dynamic segmentation) -----------------------
    def _new_planner(self, total: int, chunks):
        from ..segments import Segment, SegmentPlanner, clamp_connections
        conns = clamp_connections(getattr(self.settings, "per_link_chunks", 2) or 2)
        min_mb = max(1, int(getattr(self.settings, "segment_min_mb", 1) or 1))
        planner = SegmentPlanner(total, max_connections=conns, min_size=min_mb * 1024 * 1024)
        if chunks is not None:  # resume: the saved map is the plan
            planner.segments = [Segment(int(s0), int(e0) - 1, int(d0)) for s0, e0, d0 in chunks]
        return planner

    def _sync_chunks(self) -> None:
        """Mirror the planner's segments into the sidecar triples."""
        planner = getattr(self, "_planner", None)
        if planner is None:
            return
        with self._state_lock:
            self._chunks = [[seg.start, seg.end + 1, min(seg.done, seg.length)]
                            for seg in planner.segments]

    def _parallel(self, total: int, chunks: list[list[int]]) -> None:
        # Prepare the file before workers open independent handles at offsets.
        self.part.parent.mkdir(parents=True, exist_ok=True)
        with self._file_lock:
            mode = "r+b" if self.part.exists() else "w+b"
            with open(self.part, mode) as f:
                f.truncate(total)

        planner = getattr(self, "_planner", None)
        if planner is None:
            planner = self._planner = self._new_planner(total, chunks)
        errors: list[Exception] = []
        err_lock = threading.Lock()

        def worker() -> None:
            while not self._abort.is_set() and not self.stop_event.is_set():
                with self._state_lock:
                    seg = planner.next_segment()
                if seg is None:
                    return
                try:
                    self._stream(seg.next_offset, seg.end + 1, seg=seg)
                except DownloadError as e:
                    if e.cancelled or self.stop_event.is_set():
                        return  # a stop is a clean request, not a failure (B9)
                    with err_lock:
                        errors.append(e)
                    self._abort.set()
                    return
                except Exception as e:
                    with err_lock:
                        errors.append(e)
                    self._abort.set()
                    return
                finally:
                    with self._state_lock:
                        planner.release(seg)

        threads = [threading.Thread(target=worker, daemon=True, name=f"dl-{self.link.id[:8]}-{i}")
                   for i in range(planner.max_connections)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self._sync_chunks()

        if self.stop_event.is_set():
            self._save_meta()
            raise _cancelled()
        if any(isinstance(error, RangeUnsupported) for error in errors):
            raise RangeUnsupported(str(next(e for e in errors if isinstance(e, RangeUnsupported))))
        if errors:
            self._save_meta()
            raise DownloadError(f"chunk failed: {errors[0]}", transient=_is_transient(errors[0]))
        if not planner.complete():
            self._save_meta()
            raise DownloadError("incomplete chunks", transient=True)
        self.link.extra["segments"] = len(planner.segments)

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

        # Clean up metadata on successful finalization
        self._drop_meta()



def _int_hdr(v: Optional[str]) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None