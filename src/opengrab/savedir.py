"""Choosing and checking the folder a download is saved to.

One place decides what a "Save to" folder may be, so the Add links dialog, the
LinkGrabber, the per-download "Change folder" action and the API all agree:
an absolute path, created only when the caller said so, and writable.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

MAX_RECENT = 8


class SaveDirError(ValueError):
    """``code`` is one of: empty, relative, missing, not_dir, not_writable, invalid."""

    def __init__(self, code: str, message: str, path: str = ""):
        super().__init__(message)
        self.code = code
        self.path = path


def validate_save_dir(raw, create: bool = False) -> Path:
    """The folder as a normalised absolute path; raises SaveDirError.

    A missing folder is created only when ``create`` is true (the UI asks the
    user first and then repeats the request with create=true).
    """
    text = str(raw or "").strip().strip('"')
    if not text:
        raise SaveDirError("empty", "Choose a folder to save to.")
    if "\x00" in text or len(text) > 1000:
        raise SaveDirError("invalid", "That is not a valid folder path.", text)
    path = Path(text)
    if not path.is_absolute():
        raise SaveDirError("relative", "Enter the full path of the folder, for example "
                           r"C:\Downloads\Movies.", text)
    path = Path(os.path.normpath(str(path)))
    if path.exists() and not path.is_dir():
        raise SaveDirError("not_dir", "That path is a file, not a folder.", str(path))
    if not path.exists():
        if not create:
            raise SaveDirError("missing", "Folder doesn't exist.", str(path))
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SaveDirError("not_writable", f"Could not create the folder: "
                               f"{exc.strerror or exc}", str(path)) from exc
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".opengrab-write-test-"):
            pass
    except OSError as exc:
        raise SaveDirError("not_writable", f"OpenGrab cannot write to that folder: "
                           f"{exc.strerror or exc}", str(path)) from exc
    return path


def record_recent(settings, folder) -> list[str]:
    """Put ``folder`` first in settings.recent_download_dirs (no duplicates, at most 8)."""
    text = str(folder)
    seen = {text.casefold()}
    out = [text]
    for old in settings.recent_download_dirs:
        if str(old).casefold() not in seen:
            seen.add(str(old).casefold())
            out.append(str(old))
    settings.recent_download_dirs = out[:MAX_RECENT]
    try:
        settings.save()
    except Exception:  # noqa: BLE001  (a read-only settings file must not block a download)
        pass
    return list(settings.recent_download_dirs)


def error_payload(exc: SaveDirError) -> tuple[int, dict]:
    """(HTTP status, JSON body) for a refused folder: 409 when only creating it would help."""
    return (409 if exc.code == "missing" else 400,
            {"error": str(exc), "code": exc.code, "path": exc.path})


def folder_from_body(settings, body: dict) -> str:
    """The "Save to" folder of an add request: "" when none was asked for (or it is
    the default folder, so a later change of the default still applies), else the
    validated absolute path, which is also remembered as a recent folder.
    Raises SaveDirError."""
    raw = (body or {}).get("save_dir")
    if raw is None or str(raw).strip() == "":
        return ""
    path = validate_save_dir(raw, create=bool((body or {}).get("create_dir")))
    record_recent(settings, path)
    try:
        default = Path(settings.download_dir).expanduser().resolve()
        if os.path.normcase(str(path.resolve())) == os.path.normcase(str(default)):
            return ""
    except OSError:
        pass
    return str(path)
