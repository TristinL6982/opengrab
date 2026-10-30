"""Optional add-ons downloaded on request (spec section 2).

The base app and installer carry no ML packages. The local OCR captcha
solver (ddddocr with onnxruntime, numpy and OpenCV) is an add-on: the user
opts in, OpenGrab downloads the exact wheels pinned in ``addons_lock.json``
from PyPI, checks each SHA-256, unpacks them into
``<data dir>/addons/<name>/site`` and adds that folder to ``sys.path``.

From a source checkout (not the frozen exe) on a platform the lock does not
cover, the add-on falls back to ``pip install --target`` into the same folder.

Nothing here runs at import time; ``activate_installed()`` is called once by
the app bootstrap and again by a solver right before it imports the package.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("opengrab.addons")

LOCK_FILE = Path(__file__).with_name("addons_lock.json")

ADDONS: dict[str, dict] = {
    "ocr": {
        "title": "Local OCR captcha solver (ddddocr)",
        "module": "ddddocr",
        "description": "Solves simple image/text captchas offline on the CPU. "
                       "Off by default; downloaded only when you install it.",
        # Model files ddddocr ships but the default solver never loads.
        "prune": ["ddddocr/common.onnx", "ddddocr/common_det.onnx"],
        "pip": ["ddddocr>=1.6.1"],
    },
}

_lock = threading.Lock()
_jobs: dict[str, dict] = {}


class AddonError(RuntimeError):
    pass


def addons_root(data_dir: Optional[Path] = None) -> Path:
    if data_dir is None:
        from .settings import app_data_dir
        data_dir = app_data_dir()
    return Path(data_dir) / "addons"


def site_dir(name: str, data_dir: Optional[Path] = None) -> Path:
    return addons_root(data_dir) / name / "site"


def load_lock() -> dict:
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"addons": {}}


def lock_matches_runtime(lock: dict) -> bool:
    """The pinned wheels only fit CPython 3.12 on Windows x64."""
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        return False
    return lock.get("python") == f"cp{sys.version_info.major}{sys.version_info.minor}"


def is_installed(name: str, data_dir: Optional[Path] = None) -> bool:
    return (site_dir(name, data_dir) / ".installed.json").is_file()


def activate_installed(data_dir: Optional[Path] = None) -> list[str]:
    """Put every installed add-on's site folder on sys.path (idempotent)."""
    added = []
    root = addons_root(data_dir)
    if not root.is_dir():
        return added
    for name in ADDONS:
        site = site_dir(name, data_dir)
        if (site / ".installed.json").is_file() and str(site) not in sys.path:
            sys.path.append(str(site))
            if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
                for libs in site.glob("*.libs"):
                    try:
                        os.add_dll_directory(str(libs))
                    except OSError:
                        pass
            added.append(name)
    return added


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def status(name: str, data_dir: Optional[Path] = None) -> dict:
    if name not in ADDONS:
        raise KeyError(name)
    spec = ADDONS[name]
    lock = load_lock().get("addons", {}).get(name, {})
    site = site_dir(name, data_dir)
    installed = is_installed(name, data_dir)
    info = {}
    if installed:
        try:
            info = json.loads((site / ".installed.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
    with _lock:
        job = dict(_jobs.get(name) or {})
    return {
        "name": name,
        "title": spec["title"],
        "description": spec["description"],
        "installed": installed,
        "installed_at": info.get("installed_at"),
        "method": info.get("method"),
        "disk_bytes": _dir_size(site) if installed else 0,
        "download_bytes": int(lock.get("download_bytes") or 0),
        "job": job or None,
    }


def _download(url: str, dest: Path, expected_sha256: str, expected_size: int,
              fetch: Optional[Callable] = None,
              on_bytes: Optional[Callable[[int], None]] = None) -> None:
    h = hashlib.sha256()
    written = 0
    if fetch is not None:
        data = fetch(url)
        h.update(data)
        dest.write_bytes(data)
        written = len(data)
        if on_bytes:
            on_bytes(written)
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenGrab addon installer"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                h.update(chunk)
                out.write(chunk)
                written += len(chunk)
                if on_bytes:
                    on_bytes(len(chunk))
    if h.hexdigest() != expected_sha256:
        raise AddonError(f"hash mismatch for {dest.name}; download refused")
    if expected_size and written != expected_size:
        raise AddonError(f"size mismatch for {dest.name}")


def _safe_extract(wheel: Path, target: Path) -> None:
    root = target.resolve()
    with zipfile.ZipFile(wheel) as zf:
        for member in zf.infolist():
            out = (target / member.filename).resolve()
            try:
                out.relative_to(root)
            except ValueError as exc:
                raise AddonError(f"unsafe path in {wheel.name}: {member.filename}") from exc
        zf.extractall(target)


def install(name: str, data_dir: Optional[Path] = None, fetch: Optional[Callable] = None,
            progress: Optional[Callable[[dict], None]] = None,
            use_pip: Optional[bool] = None) -> dict:
    """Install an add-on synchronously. ``fetch(url) -> bytes`` is for tests."""
    if name not in ADDONS:
        raise KeyError(name)
    spec = ADDONS[name]
    lock = load_lock()
    entry = lock.get("addons", {}).get(name)
    if use_pip is None:
        use_pip = not (entry and lock_matches_runtime(lock)) and fetch is None
    root = addons_root(data_dir) / name
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="site-", dir=str(root)))
    state = {"done": 0, "total": int((entry or {}).get("download_bytes") or 0), "step": "starting"}

    def report(**kw):
        state.update(kw)
        if progress:
            progress(dict(state))

    try:
        if use_pip:
            if getattr(sys, "frozen", False):
                raise AddonError("this platform has no pinned add-on wheels; "
                                 "install from a source checkout instead")
            report(step="pip install")
            subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                            "--no-input", "--target", str(staging), *spec["pip"]],
                           check=True, capture_output=True, timeout=1800)
            method = "pip"
        else:
            if not entry:
                raise AddonError(f"no pinned wheels for add-on {name!r}")
            downloads = Path(tempfile.mkdtemp(prefix="dl-", dir=str(root)))
            try:
                for wheel in entry["wheels"]:
                    report(step=f"downloading {wheel['filename']}")
                    dest = downloads / wheel["filename"]
                    _download(wheel["url"], dest, wheel["sha256"], int(wheel.get("size") or 0),
                              fetch=fetch,
                              on_bytes=lambda n: report(done=state["done"] + n))
                for wheel in entry["wheels"]:
                    report(step=f"unpacking {wheel['filename']}")
                    _safe_extract(downloads / wheel["filename"], staging)
            finally:
                shutil.rmtree(downloads, ignore_errors=True)
            method = "wheels"
        for rel in spec.get("prune", []):
            try:
                (staging / rel).unlink()
            except FileNotFoundError:
                pass
        (staging / ".installed.json").write_text(json.dumps({
            "name": name, "method": method, "installed_at": time.time(),
            "wheels": [w["filename"] for w in (entry or {}).get("wheels", [])] if method == "wheels" else [],
        }), encoding="utf-8")
        final = site_dir(name, data_dir)
        if final.exists():
            shutil.rmtree(final)
        staging.rename(final)
        report(step="installed")
        activate_installed(data_dir)
        return status(name, data_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def install_async(name: str, data_dir: Optional[Path] = None, on_done=None) -> dict:
    """Start an install in a background thread; poll status(name)['job']."""
    if name not in ADDONS:
        raise KeyError(name)
    with _lock:
        job = _jobs.get(name)
        if job and job.get("running"):
            return dict(job)
        job = {"running": True, "step": "queued", "done": 0, "total": 0, "error": ""}
        _jobs[name] = job

    def progress(state):
        with _lock:
            job.update(state)

    def run():
        try:
            install(name, data_dir, progress=progress)
            ok, err = True, ""
        except Exception as exc:  # reported to the UI
            log.warning("add-on %s install failed: %s", name, exc)
            ok, err = False, str(exc)[:300]
        with _lock:
            job.update(running=False, ok=ok, error=err)
        if on_done:
            try:
                on_done(name, ok, err)
            except Exception:
                pass

    threading.Thread(target=run, name=f"addon-{name}", daemon=True).start()
    with _lock:
        return dict(job)


def remove(name: str, data_dir: Optional[Path] = None) -> bool:
    if name not in ADDONS:
        raise KeyError(name)
    site = site_dir(name, data_dir)
    if str(site) in sys.path:
        sys.path.remove(str(site))
    root = addons_root(data_dir) / name
    if not root.exists():
        return False
    shutil.rmtree(root, ignore_errors=True)
    return True


def importable(module: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False
