"""Packagizer matching, preview, path safety, and collision checks."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from opengrab.engine import manager as manager_module
from opengrab.engine.manager import DownloadManager
from opengrab.engine.packagizer import PackagizerRule, plan_packagizer
from opengrab.models import Link, LinkStatus
from opengrab.settings import Settings


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="opengrab-packagizer-") as td:
        root = Path(td)
        downloads = root / "downloads"
        downloads.mkdir()
        rule = PackagizerRule(name="zip files", pattern="*.zip",
                              target_subdir="archives/{host}",
                              filename_template="{stem}-saved.{ext}")
        plan = plan_packagizer([rule.model_dump()], "https://example.com/a.zip",
                               "a.zip", downloads)
        assert plan["matched"] and plan["relative_path"] == "archives/example.com/a-saved.zip"
        print("[PASS] glob rule and safe template preview")

        collision = downloads / plan["relative_path"]
        collision.parent.mkdir(parents=True)
        collision.write_text("preserve", encoding="utf-8")
        plan2 = plan_packagizer([rule.model_dump()], "https://example.com/a.zip",
                                "a.zip", downloads)
        assert plan2["collision_exists"] and plan2["filename"] == "a-saved_1.zip"
        assert collision.read_text() == "preserve"
        print("[PASS] preview preserves existing files and numbers collisions")

        for bad in ("../outside", "C:\\outside", "/absolute", "folder/NUL"):
            try:
                PackagizerRule(pattern="*.zip", target_subdir=bad)
                raise AssertionError(f"unsafe destination accepted: {bad}")
            except ValueError:
                pass
        try:
            PackagizerRule(pattern="*.zip", target_subdir="safe",
                           filename_template="{host.__class__}")
            raise AssertionError("unsafe template field accepted")
        except ValueError:
            pass
        print("[PASS] traversal and unsafe template rejected")

        data = root / "data"
        old_data = os.environ.get("OPENGRAB_DATA_DIR")
        os.environ["OPENGRAB_DATA_DIR"] = str(data)
        manager = DownloadManager(Settings(
            download_dir=str(downloads), packagizer_rules=[rule.model_dump()],
            auto_extract_archives=False))

        class FixtureDownload:
            def __init__(self, link, url, dest, **kwargs):
                self.dest, self.name = Path(dest), link.filename

            def run(self):
                target = self.dest / self.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("fixture", encoding="utf-8")
                return target

        try:
            for _ in range(2):
                link = Link(url="https://example.com/report.zip", filename="report.zip")
                with patch.object(manager_module, "LinkDownload", FixtureDownload):
                    manager._run_download(link)
                assert link.status == LinkStatus.FINISHED
                assert "archives/example.com" in link.path.replace("\\", "/")
            assert (downloads / "archives/example.com/report-saved.zip").read_text() == "fixture"
            assert (downloads / "archives/example.com/report-saved_1.zip").read_text() == "fixture"
            print("[PASS] download manager applies rules without overwriting files")
        finally:
            manager.shutdown()
            if old_data is None:
                os.environ.pop("OPENGRAB_DATA_DIR", None)
            else:
                os.environ["OPENGRAB_DATA_DIR"] = old_data
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
