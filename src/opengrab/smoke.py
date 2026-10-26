"""Isolated end-to-end smoke check used by ``opengrab --smoke-test``."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.request
import py7zr
from pathlib import Path


SMOKE_URL = (
    "https://files.pythonhosted.org/packages/b3/38/89ba8ad64ae25be8de66a6d4"
    "63314cf1eb366222074cfda9ee839c56a4b4/mdurl-0.1.2-py3-none-any.whl"
)
SMOKE_SIZE = 9979
SMOKE_SHA256 = "84008a41e51615a49fc9966191ff91509e3c40b939176e643fd50a5c2196b8f8"


def run_smoke_test(timeout: int = 120) -> bool:
    """Check plugin initialization, local API, and a real verified download."""
    temp = Path(tempfile.mkdtemp(prefix="opengrab-smoke-"))
    previous_data_dir = os.environ.get("OPENGRAB_DATA_DIR")
    os.environ["OPENGRAB_DATA_DIR"] = str(temp / "data")
    from .engine.manager import DownloadManager
    from .server import ApiServer
    from .settings import Settings

    manager = None
    server = None
    try:
        downloads = temp / "downloads"
        downloads.mkdir()
        settings = Settings(download_dir=str(downloads), api_port=0,
                           max_parallel=1, per_link_chunks=1)
        manager = DownloadManager(settings)
        manager.start()
        if not manager.registry.snapshot():
            raise RuntimeError("no plugins were discovered")
        server = ApiServer(settings, manager, host="127.0.0.1", port=0)
        port = server.start()
        base = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(base + "/api/ping", timeout=10) as response:
            ping = json.loads(response.read())
        if ping.get("ok") is not True or ping.get("app") != "OpenGrab":
            raise RuntimeError(f"API health check failed: {ping}")
        for asset in ("/", "/app.js", "/style.css", "/manifest.webmanifest",
                      "/icon-192.png", "/icon-512.png"):
            with urllib.request.urlopen(base + asset, timeout=10) as response:
                if response.status != 200 or not response.read(64):
                    raise RuntimeError(f"static asset check failed: {asset}")

        link = manager.add_url(SMOKE_URL, auto_download=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = next((item for item in manager.all_links()
                            if item.id == link.id), None)
            if current and current.status.value in {"finished", "error"}:
                break
            time.sleep(0.25)
        else:
            raise TimeoutError("smoke download did not finish in time")

        if current.status.value != "finished":
            raise RuntimeError(f"smoke download failed: {current.error}")
        target = downloads / Path(current.filename).name
        if not target.is_file() or target.stat().st_size != SMOKE_SIZE:
            raise RuntimeError("smoke download has the wrong size")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != SMOKE_SHA256:
            raise RuntimeError(f"smoke download checksum mismatch: {digest}")
        from .engine.archives import extract_archive
        archive_source = downloads / "archive-smoke.txt"
        archive_source.write_text("7z smoke verification", encoding="utf-8")
        archive_path = downloads / "archive-smoke.7z"
        with py7zr.SevenZipFile(archive_path, "w") as archive:
            archive.write(archive_source, archive_source.name)
        archive_source.unlink()
        extraction = extract_archive(archive_path)
        extracted_file = Path(extraction["path"]) / "archive-smoke.txt"
        if extracted_file.read_text(encoding="utf-8") != "7z smoke verification":
            raise RuntimeError("7z archive smoke check failed")
        print("OpenGrab smoke test passed (plugins, API, UI, download, SHA-256, 7z).")
        return True
    except Exception as exc:
        print(f"OpenGrab smoke test failed: {exc}")
        return False
    finally:
        if server is not None:
            server.stop()
        if manager is not None:
            manager.shutdown()
        if previous_data_dir is None:
            os.environ.pop("OPENGRAB_DATA_DIR", None)
        else:
            os.environ["OPENGRAB_DATA_DIR"] = previous_data_dir


if __name__ == "__main__":
    raise SystemExit(0 if run_smoke_test() else 1)
