"""Safety-focused tests for bounded ZIP/TAR extraction."""
from __future__ import annotations

import tarfile
import tempfile
import zipfile
import os
from pathlib import Path
from unittest.mock import patch

from opengrab.engine.archives import ArchiveError, extract_archive
from opengrab.engine import manager as manager_module
from opengrab.engine.manager import DownloadManager
from opengrab.models import Link, LinkStatus
from opengrab.settings import Settings


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="opengrab-archive-") as td:
        root = Path(td)
        good = root / "good.zip"
        with zipfile.ZipFile(good, "w") as archive:
            archive.writestr("folder/readme.txt", "safe")
        result = extract_archive(good)
        assert result["status"] == "extracted" and result["files"] == 1
        assert (root / "good/folder/readme.txt").read_text() == "safe"
        assert good.exists(), "original archive must be preserved"
        print("[PASS] ZIP extracts safely and preserves original")
        try:
            extract_archive(good)
            raise AssertionError("pre-existing extraction folder was overwritten")
        except ArchiveError:
            pass
        assert (root / "good/folder/readme.txt").read_text() == "safe"
        print("[PASS] existing extraction folder is preserved")

        evil = root / "evil.zip"
        with zipfile.ZipFile(evil, "w") as archive:
            archive.writestr("../escaped.txt", "no")
        try:
            extract_archive(evil)
            raise AssertionError("path traversal archive was accepted")
        except ArchiveError:
            pass
        assert not (root.parent / "escaped.txt").exists()
        print("[PASS] ZIP path traversal rejected")

        device = root / "device.zip"
        with zipfile.ZipFile(device, "w") as archive:
            archive.writestr("folder/NUL.txt", "no")
        try:
            extract_archive(device)
            raise AssertionError("Windows device path was accepted")
        except ArchiveError:
            pass
        print("[PASS] cross-platform device paths rejected")

        over_limit = root / "many.zip"
        with zipfile.ZipFile(over_limit, "w") as archive:
            archive.writestr("a.txt", "a")
            archive.writestr("b.txt", "b")
        try:
            extract_archive(over_limit, max_files=1)
            raise AssertionError("file-count limit was ignored")
        except ArchiveError:
            pass
        print("[PASS] ZIP file-count limit enforced")
        try:
            extract_archive(over_limit, max_bytes=1)
            raise AssertionError("expanded-size limit was ignored")
        except ArchiveError:
            pass
        print("[PASS] ZIP expanded-size limit enforced")

        tar_path = root / "linked.tar"
        with tarfile.open(tar_path, "w") as archive:
            item = tarfile.TarInfo("link")
            item.type = tarfile.SYMTYPE
            item.linkname = "../../outside"
            archive.addfile(item)
        try:
            extract_archive(tar_path)
            raise AssertionError("TAR symbolic link was accepted")
        except ArchiveError:
            pass
        print("[PASS] TAR links and special files rejected")

        data = root / "data"
        downloads = root / "downloads"
        downloads.mkdir()
        old_data = os.environ.get("OPENGRAB_DATA_DIR")
        os.environ["OPENGRAB_DATA_DIR"] = str(data)
        manager = DownloadManager(Settings(download_dir=str(downloads)))

        class FixtureDownload:
            def __init__(self, link, url, dest, **kwargs):
                self.dest, self.name = Path(dest), link.filename

            def run(self):
                target = self.dest / self.name
                target.write_bytes(good.read_bytes())
                return target

        try:
            link = Link(url="https://example.invalid/good.zip", filename="download.zip")
            with patch.object(manager_module, "LinkDownload", FixtureDownload):
                manager._run_download(link)
            assert link.status == LinkStatus.FINISHED
            assert link.extra["archive_extraction"]["status"] == "extracted"
            assert (downloads / "download/folder/readme.txt").read_text() == "safe"
            print("[PASS] completed downloads trigger extraction safely")
        finally:
            manager.shutdown()
            if old_data is None:
                os.environ.pop("OPENGRAB_DATA_DIR", None)
            else:
                os.environ["OPENGRAB_DATA_DIR"] = old_data
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
