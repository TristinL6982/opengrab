"""Removing downloads while they run must free their slots: the next
downloads start. Found while taking README screenshots (2026-10-30)."""
import time

from tests.helpers.apiserver import req, running_server
from tests.helpers.filesite import FileSite

MB = 1024 * 1024


def _wait(pred, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


def test_removing_running_downloads_frees_their_slots():
    site = FileSite()
    site.delay_per_64k = 0.05  # slow enough to be mid-transfer when removed
    try:
        import os
        urls = [site.add(f"/r/big{i}.bin", os.urandom(6 * MB)) for i in range(4)]
        small = site.add("/r/small.bin", os.urandom(256 * 1024))
        with running_server({"max_parallel": 4, "per_link_chunks": 8, "speed_limit_kbps": 1800}) as srv:
            st, added = req(srv, "POST", "/api/urls", body={"urls": urls})
            assert st == 200
            _wait(lambda: req(srv, "GET", "/api/stats")[1]["counts"]["downloading"] == 4)
            time.sleep(2)  # transfers are mid-flight and throttled
            # the limit was changed to "none" right before the deletes when this stalled
            req(srv, "POST", "/api/settings", body={"speed_limit_kbps": 0})
            for l in added:
                st, _ = req(srv, "DELETE", f"/api/links/{l['id']}")
                assert st in (200, 204)
            st, new = req(srv, "POST", "/api/urls", body={"urls": [small]})
            lid = new[0]["id"]
            link = _wait(lambda: srv.manager.get(lid).status.value in ("finished", "error") and srv.manager.get(lid),
                         timeout=25)
            assert link.status.value == "finished", link.error
            assert req(srv, "GET", "/api/stats")[1]["counts"]["downloading"] == 0
    finally:
        site.close()


def test_lifting_the_speed_limit_releases_waiting_chunk_threads():
    """Unit view of the same bug: a thread waiting in the limiter must return
    once the limit becomes "none", and at once when its download is stopped."""
    import threading
    from opengrab.engine.downloader import _RateLimiter
    lim = _RateLimiter(1024)  # 1 KiB/s: a 64 KiB piece would wait ~60 s
    done = threading.Event()
    threading.Thread(target=lambda: (lim.consume(64 * 1024), done.set()), daemon=True).start()
    time.sleep(0.3)
    assert not done.is_set()
    lim.set_rate(0)  # "no limit"
    assert done.wait(2), "consume() kept waiting after the limit was lifted"

    lim = _RateLimiter(1024)
    stop, done = threading.Event(), threading.Event()
    threading.Thread(target=lambda: (lim.consume(64 * 1024, stop), done.set()), daemon=True).start()
    time.sleep(0.3)
    stop.set()
    assert done.wait(2), "consume() ignored the stop request"
