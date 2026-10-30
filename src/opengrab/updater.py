"""M14: in-app updates from signed GitHub releases, with rollback.

A release carries ``OpenGrab-<version>-win64.zip`` (the onedir build, with
an ``OpenGrab`` folder inside) and ``OpenGrab-<version>-win64.zip.sig``: an
Ed25519 signature (base64) over the zip's bytes, made in CI with the release
key. The public half is ``RELEASE_KEYS`` below. An update is installed only
when the signature checks out, so a hijacked download or release page
cannot push code.

Installing never touches the running copy:
1. download and verify, unpack into the user's data folder
   (updates/OpenGrab.new) and run ``OpenGrab.exe --smoke-test`` there
2. a small script waits for this process to exit, copies the new version
   next to the install folder, renames the running folder to <name>.old and
   the new one into its place, then starts it. If a step fails, the script
   puts the old folder back. When the install folder is not writable (the
   installer's default is Program Files) the script asks for administrator
   rights (UAC).
Rollback swaps <name>.old back the same way.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import __version__

REPO = "TristinL6982/opengrab"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
# Ed25519 public keys (hex) allowed to sign releases. Empty until the
# maintainer creates the release key (tools/release_key.py); until then the
# updater reports updates but refuses to install them.
RELEASE_KEYS: list[str] = []


class UpdateError(Exception):
    pass


def parse_version(v: str) -> tuple:
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)(?:[-.]?(a|b|rc)(\d+))?", (v or "").strip())
    if not m:
        return (0, 0, 0, "", 0)
    pre = m.group(4) or "~"  # "~" sorts after a/b/rc: a final release beats its pre-releases
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre, int(m.group(5) or 0))


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


@dataclass
class Release:
    version: str
    notes: str
    zip_url: str
    sig_url: str
    page: str


def asset_name(version: str) -> str:
    return f"OpenGrab-{version.lstrip('v')}-win64.zip"


def parse_release(data: dict) -> Optional[Release]:
    tag = str(data.get("tag_name") or "")
    if not tag or data.get("draft") or data.get("prerelease"):
        return None
    want = asset_name(tag)
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets") or []}
    if want not in assets or want + ".sig" not in assets:
        return None
    return Release(version=tag.lstrip("v"), notes=str(data.get("body") or "")[:4000],
                   zip_url=assets[want], sig_url=assets[want + ".sig"], page=str(data.get("html_url") or ""))


def _get(url: str, timeout: float = 30) -> bytes:
    if not url.startswith("https://"):
        raise UpdateError("updates are only downloaded over HTTPS")
    req = urllib.request.Request(url, headers={"User-Agent": f"OpenGrab/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check(fetch: Callable[[str], bytes] = _get) -> dict:
    rel = parse_release(json.loads(fetch(API)))
    if rel is None:
        return {"current": __version__, "available": False}
    return {"current": __version__, "available": is_newer(rel.version), "version": rel.version,
            "notes": rel.notes, "page": rel.page, "installable": bool(RELEASE_KEYS) and is_frozen()}


def verify(data: bytes, signature_b64: str, keys: Optional[list] = None) -> bool:
    from .engine.plugin_policy import verify_signature
    keys = RELEASE_KEYS if keys is None else keys
    return bool(keys) and verify_signature(signature_b64.strip(), data, keys)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent


def staging_dir() -> Path:
    from .settings import app_data_dir
    return Path(app_data_dir()) / "updates"


def stage(zip_bytes: bytes, sig_b64: str, where: Path, keys: Optional[list] = None,
          smoke: Optional[Callable[[Path], bool]] = None) -> Path:
    """Verify, unpack into ``where``/OpenGrab.new and smoke-test it."""
    if not verify(zip_bytes, sig_b64, keys):
        raise UpdateError("the update's signature is not valid; nothing was installed")
    where.mkdir(parents=True, exist_ok=True)
    new = where / "OpenGrab.new"
    shutil.rmtree(new, ignore_errors=True)
    with tempfile.TemporaryDirectory(dir=where) as tmp:
        zpath = Path(tmp) / "update.zip"
        zpath.write_bytes(zip_bytes)
        with zipfile.ZipFile(zpath) as z:
            for name in z.namelist():  # no absolute paths or "..": stay inside tmp
                target = (Path(tmp) / "x" / name).resolve()
                if not str(target).startswith(str((Path(tmp) / "x").resolve())):
                    raise UpdateError(f"unsafe path in the update: {name}")
            z.extractall(Path(tmp) / "x")
        inner = Path(tmp) / "x" / "OpenGrab"
        if not inner.is_dir():
            raise UpdateError("the update has no OpenGrab folder")
        shutil.move(str(inner), str(new))
    ok = (smoke or _smoke)(new)
    if not ok:
        shutil.rmtree(new, ignore_errors=True)
        raise UpdateError("the new version failed its self-test; nothing was installed")
    return new


def _smoke(folder: Path) -> bool:
    exe = folder / "OpenGrab.exe"
    try:
        return subprocess.run([str(exe), "--smoke-test"], timeout=120,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def swap(root: Path, incoming: Path, keep: Path) -> None:
    """root -> keep, incoming -> root; undo the first rename if the second fails."""
    shutil.rmtree(keep, ignore_errors=True)
    os.replace(root, keep)
    try:
        os.replace(incoming, root)
    except OSError:
        os.replace(keep, root)
        raise


def swap_script(root: Path, incoming: Path, keep: Path, pid: int, restart: bool = True) -> str:
    """A cmd script that waits for ``pid`` to exit and then swaps folders.

    ``incoming`` may be on another drive (the staging folder), so it is
    first copied next to ``root`` with robocopy."""
    q = lambda p: str(p).replace('"', "")
    exe = q(root / "OpenGrab.exe")
    near = root.with_name(root.name + ".incoming")
    sysdir = r"%SystemRoot%\System32"  # full paths: Git's "find" may come first on PATH
    lines = [
        "@echo off",
        "setlocal",
        ":wait",
        f'"{sysdir}\\tasklist.exe" /FI "PID eq {pid}" /NH /FO CSV 2>nul'
        f' | "{sysdir}\\find.exe" "{pid}" >nul'
        f' && ("{sysdir}\\ping.exe" -n 2 127.0.0.1 >nul & goto wait)',
        f'if exist "{q(near)}" rmdir /s /q "{q(near)}"',
        f'"{sysdir}\\robocopy.exe" "{q(incoming)}" "{q(near)}" /E /MOVE /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul',
        "if errorlevel 8 goto fail",
        f'if exist "{q(keep)}" rmdir /s /q "{q(keep)}"',
        f'move "{q(root)}" "{q(keep)}" >nul || goto fail',
        f'move "{q(near)}" "{q(root)}" >nul || (move "{q(keep)}" "{q(root)}" >nul & goto fail)',
    ]
    # explorer.exe starts OpenGrab as the signed-in user, even when this
    # script runs with administrator rights
    if restart:
        lines.append(f'start "" explorer.exe "{exe}"')
    lines += ["exit /b 0", ":fail"]
    if restart:  # the old version is still in place: start it again
        lines.append(f'start "" explorer.exe "{exe}"')
    lines.append("exit /b 1")
    return "\r\n".join(lines) + "\r\n"


def writable(folder: Path) -> bool:
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".og-write-test-"):
            return True
    except OSError:
        return False


def launch_swap(root: Path, incoming: Path, keep: Path) -> None:
    script = staging_dir() / "OpenGrab-update.cmd"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(swap_script(root, incoming, keep, os.getpid()), encoding="mbcs" if os.name == "nt" else "utf-8",
                      errors="replace")
    if writable(root.parent):
        subprocess.Popen(["cmd", "/c", str(script)], cwd=str(script.parent),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                         | getattr(subprocess, "DETACHED_PROCESS", 0))
        return
    import ctypes  # Program Files: ask for administrator rights
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f'/c "{script}"', str(script.parent), 0)
    if rc <= 32:
        raise UpdateError("administrator rights are needed to replace the program files")


def rollback_available(root: Optional[Path] = None) -> bool:
    root = root or install_dir()
    return (root.with_name(root.name + ".old") / "OpenGrab.exe").is_file()
