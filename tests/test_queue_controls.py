"""Priority queue ordering and shared bandwidth limiter wiring."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from opengrab.engine.downloader import _RateLimiter
from opengrab.engine.manager import DownloadManager
from opengrab.models import Link, LinkStatus
from opengrab.settings import Settings


def main() -> int:
    limiter = _RateLimiter(1000)
    limiter._bal = 25
    limiter.set_rate(1000)
    assert limiter._bal == 25, "reusing a global limiter must not refill burst credit"
    print("[PASS] starting another link does not refill the global burst allowance")

    with tempfile.TemporaryDirectory(prefix="opengrab-queue-") as tmp:
        os.environ["OPENGRAB_DATA_DIR"] = str(Path(tmp) / "data")
        settings = Settings(download_dir=str(Path(tmp) / "downloads"),
                            speed_limit_kbps=128)
        manager = DownloadManager(settings)
        # Persistence is covered by e2e_api.py; keep this focused test's temp
        # directory free of the manager's debounced background writer.
        manager._persist = lambda: None
        old_low = Link(url="https://example.invalid/low", priority=0,
                       created=1.0, status=LinkStatus.WAITING)
        old_high = Link(url="https://example.invalid/high", priority=5,
                        created=2.0, status=LinkStatus.WAITING)
        new_high = Link(url="https://example.invalid/new-high", priority=5,
                        created=3.0, status=LinkStatus.WAITING)
        manager._links.update({x.id: x for x in (old_low, old_high, new_high)})
        for link in (old_low, old_high, new_high):
            manager._ready_put(link.id)
        order = [manager._ready.get_nowait()[2] for _ in range(3)]
        assert order == [old_high.id, new_high.id, old_low.id]
        print("[PASS] queued links sort by descending priority then creation time")

        target = Path(tmp) / "downloads" / "fixture.bin"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"ok")
        link = Link(url="https://example.invalid/file", direct_url="https://example.invalid/file",
                    filename="fixture.bin", status=LinkStatus.WAITING)
        manager._links[link.id] = link
        captured = {}

        class FakeDownload:
            def __init__(self, *_args, **kwargs):
                captured["limiter"] = kwargs["rate_limiter"]

            def run(self):
                return target

        with patch("opengrab.engine.manager.LinkDownload", FakeDownload):
            manager._run_download(link)
        assert captured["limiter"] is manager._global_limiter
        assert manager._global_limiter.rate == 128 * 1024
        print("[PASS] active downloads share the configured aggregate rate limiter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
