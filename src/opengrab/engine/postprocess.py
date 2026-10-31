"""W10: after a download finishes - archive volume sets and virus scanning.

* archives (top-level M11q module): multi-part sets (name.part1.rar,
  name.7z.001, name.r00 ...) are extracted once, from the first volume, only
  when every volume is on disk and no other part is still downloading.
* avscan (M11h): optional, off by default. Windows Defender (MpCmdRun) or a
  custom command line scans the finished file in the background so the queue
  never waits; an infected file is flagged, announced and optionally moved to
  a quarantine folder.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from .. import archives as volsets
from .. import avscan

log = logging.getLogger("opengrab.postprocess")


# ---------------------------------------------------------------- archives
def set_members(path: Path) -> tuple[str, list[str]] | None:
    """(set key, volume names on disk) when path is part of a multi-part set."""
    info = volsets.volume_info(path.name)
    if info is None:
        return None
    names = [p.name for p in path.parent.iterdir()
             if p.is_file() and (volsets.volume_info(p.name) or ("", 0))[0] == info[0]]
    if len(names) == 1 and info[0] == path.name.lower():
        # a plain name.rar with no .r00/.r01 siblings is an ordinary archive;
        # name.part1.rar or name.7z.001 alone is an incomplete set
        return None
    return info[0], names


def extract_ready(manager, link, path: Path) -> dict | None:
    """Extract path (or its whole volume set) if ready. Returns the result for
    link.extra["archive_extraction"], or None when it is not an archive."""
    from .archives import extract_archive, is_supported_archive
    s = manager.settings
    members = set_members(path)
    passwords = list(link.extra.get("archive_passwords") or [])
    if members is not None:
        key, names = members
        waiting = [l for l in manager.all_links()
                   if l.id != link.id and l.status.value not in ("finished", "error", "skipped")
                   and l.filename and (volsets.volume_info(l.filename) or ("", 0))[0] == key]
        if waiting or not volsets.is_set_complete(names):
            return {"status": "waiting", "message": "waiting for the other parts of this archive"}
        first = volsets.first_volume(names)
        if first is None:
            return {"status": "waiting", "message": "the first part of this archive is missing"}
        target = path.parent / first
        for other in manager.all_links():  # passwords from any part of the set
            if other.filename and (volsets.volume_info(other.filename) or ("", 0))[0] == key:
                passwords.extend(p for p in other.extra.get("archive_passwords") or [] if p not in passwords)
    else:
        target = path
        if not is_supported_archive(target):
            return None
    kwargs = dict(
        max_files=max(1, min(s.archive_max_files, 100000)),
        max_bytes=max(1, s.archive_max_expanded_gib) * 1024 * 1024 * 1024,
        password_file=s.archive_password_file,
        extra_passwords=passwords,
        seven_zip=getattr(s, "archive_7zip_path", ""),
    )
    if members is None:
        return extract_archive(target, **kwargs)
    from .archives import ArchiveError
    try:
        return extract_archive(target, **kwargs)
    except ArchiveError as exc:
        # File names cannot say how many parts a set has (1 and 2 look
        # complete without 3). A set that fails is most likely incomplete;
        # it is tried again when another part finishes.
        text = str(exc).lower()
        if any(w in text for w in ("limit", "too many", "unsafe", "escapes", "links are not")):
            raise  # safety refusals are final
        return {"status": "waiting",
                "message": "some parts of this archive seem to be missing (" + str(exc)[:200] + ")"}


# ---------------------------------------------------------------- virus scan
def _runner(argv: list, timeout: float):
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


class VirusScan:
    def __init__(self, settings, runner=_runner, exists=os.path.exists):
        self.settings = settings
        self.runner = runner
        self.exists = exists
        self.history: list[dict] = []
        self._lock = threading.Lock()

    def config(self) -> avscan.ScanConfig:
        try:
            return avscan.config_from_dict(getattr(self.settings, "avscan", None) or {})
        except (TypeError, ValueError):
            return avscan.ScanConfig()

    def enabled(self) -> bool:
        return self.config().enabled

    # -- VirusTotal (hash lookup, optional upload) ---------------------------
    def vt_config(self):
        from .. import virustotal
        try:
            return virustotal.config_from_dict(getattr(self.settings, "virustotal", None) or {})
        except (TypeError, ValueError):
            return virustotal.VtConfig()

    def vt_enabled(self) -> bool:
        cfg = self.vt_config()
        return cfg.enabled and bool(cfg.api_key)

    def vt_check(self, path: str):
        from .. import virustotal
        res = virustotal.VirusTotal(self.vt_config()).check(path)
        with self._lock:
            self.history.append({"path": path, "status": res.status, "code": None, "scanner": "virustotal",
                                 "detail": res.detail, "link": res.link})
            self.history = self.history[-200:]
        return res

    def scan(self, path: str) -> avscan.ScanResult:
        res = avscan.Scanner(self.config(), self.runner, self.exists).scan(path)
        with self._lock:
            self.history.append({"path": res.path, "status": res.status, "code": res.code,
                                 "detail": res.detail[-500:]})
            self.history = self.history[-200:]
        return res

    def quarantine(self, path: Path, download_root: Path) -> Path:
        qdir = download_root / "quarantine"
        qdir.mkdir(parents=True, exist_ok=True)
        target = qdir / path.name
        i = 1
        while target.exists():
            target = qdir / f"{path.stem}_{i}{path.suffix}"
            i += 1
        shutil.move(str(path), str(target))
        return target
