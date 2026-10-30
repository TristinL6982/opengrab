"""W6: dynamic segmentation (IDM-style) in the real downloader."""
import hashlib
import json
import os
import threading
import time

from opengrab.engine.downloader import LinkDownload
from opengrab.models import Link
from opengrab.settings import Settings
from tests.helpers.filesite import FileSite

MB = 1024 * 1024


def _run(url, tmp_path, name, **settings):
    link = Link(url=url, filename=name)
    dl = LinkDownload(link, url, tmp_path, settings=Settings(download_dir=str(tmp_path), **settings))
    return link, dl


def test_slow_range_is_split_and_file_is_correct(tmp_path):
    site = FileSite()
    try:
        data = os.urandom(24 * MB)
        url = site.add("/big.bin", data)
        # the first quarter crawls, so free connections must take over half of it
        site.slow_below, site.slow_delay = 6 * MB, 0.03
        link, dl = _run(url, tmp_path, "big.bin", per_link_chunks=4, segment_min_mb=1)
        path = dl.run()
        assert hashlib.sha256(open(path, "rb").read()).digest() == hashlib.sha256(data).digest()
        assert link.extra["segments"] > 4
    finally:
        site.close()


def test_stop_and_resume_after_splits(tmp_path):
    site = FileSite()
    try:
        data = os.urandom(16 * MB)
        url = site.add("/r.bin", data)
        # slow enough that the split is checkpointed long before the end
        site.slow_below, site.slow_delay = 4 * MB, 0.08
        site.delay_per_64k = 0.03
        link, dl = _run(url, tmp_path, "r.bin", per_link_chunks=4, segment_min_mb=1)
        errors: list = []
        t = threading.Thread(target=lambda: _swallow(dl.run, errors), daemon=True)
        t.start()
        meta = tmp_path / "r.bin.part.json"
        deadline = time.time() + 30
        while time.time() < deadline:
            if meta.exists() and len(json.loads(meta.read_text())["chunks"]) > 4:
                break
            time.sleep(0.05)
        dl.stop_event.set()
        t.join(15)
        assert meta.exists(), f"no checkpoint was written; the download ended with {errors!r}"
        saved = json.loads(meta.read_text())["chunks"]
        assert len(saved) > 4
        # ranges tile the file without gaps or overlaps
        assert saved[0][0] == 0 and saved[-1][1] == len(data)
        assert all(a[1] == b[0] for a, b in zip(saved, saved[1:]))
        site.slow_delay = site.delay_per_64k = 0
        link2, dl2 = _run(url, tmp_path, "r.bin", per_link_chunks=4, segment_min_mb=1)
        path = dl2.run()
        assert hashlib.sha256(open(path, "rb").read()).digest() == hashlib.sha256(data).digest()
    finally:
        site.close()


def test_connections_are_clamped_to_16(tmp_path):
    from opengrab.segments import clamp_connections
    assert clamp_connections(64) == 16 and clamp_connections(0) == 1


def test_benchmark_multi_connection_beats_single_connection(tmp_path):
    """Spec 6a: measure against a single connection. Each connection is
    throttled by the server, as on real file hosts."""
    site = FileSite()
    try:
        data = os.urandom(8 * MB)
        url = site.add("/bench.bin", data)
        site.delay_per_64k = 0.01  # about 6 MB/s per connection
        # best of two runs each: a shared CI runner can stall one run (seen once
        # on GitHub's 2-core Windows runner); the bar below stays the same
        t_single = t_multi = float("inf")
        for i in range(2):
            (tmp_path / f"one{i}").mkdir()
            t0 = time.monotonic()
            _, single = _run(url, tmp_path / f"one{i}", "bench.bin", per_link_chunks=1)
            single.run()
            t_single = min(t_single, time.monotonic() - t0)
            (tmp_path / f"eight{i}").mkdir()
            t0 = time.monotonic()
            link, multi = _run(url, tmp_path / f"eight{i}", "bench.bin", per_link_chunks=8, segment_min_mb=1)
            path = multi.run()
            t_multi = min(t_multi, time.monotonic() - t0)
            assert hashlib.sha256(open(path, "rb").read()).digest() == hashlib.sha256(data).digest()
        print(f"single connection {t_single:.2f}s, 8 connections {t_multi:.2f}s, speed-up {t_single / t_multi:.1f}x")
        # 4.3x on an 28-core PC, 2.2x on a 2-core CI runner: require a clear win
        assert t_multi < t_single / 1.5
    finally:
        site.close()


def _swallow(fn, errors=None):
    """Run fn; a stop raises, so keep the exception for the test's message."""
    try:
        fn()
    except Exception as exc:
        if errors is not None:
            errors.append(exc)
