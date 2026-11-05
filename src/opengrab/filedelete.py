"""Delete a link's own files safely (used by "Remove and delete files from this PC").

Only regular files that resolve to a place inside the allowed roots are touched;
nothing outside them is ever followed, and folders are never deleted. On Windows
the files go to the Recycle Bin (SHFileOperationW with FOF_ALLOWUNDO); elsewhere,
or if the Recycle Bin refuses, they are deleted normally.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Iterable

# Tests switch this off so they never touch the real Recycle Bin.
USE_RECYCLE_BIN = True

_FO_DELETE = 0x0003
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOERRORUI = 0x0400


def _recycle(path: Path) -> bool:
    """Move one file to the Recycle Bin. False when that is not possible."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = _FO_DELETE
    op.pFrom = str(path) + "\0\0"  # the list is double-null-terminated
    op.pTo = None
    op.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_NOERRORUI | _FOF_SILENT
    try:
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except (OSError, AttributeError):
        return False
    return rc == 0 and not op.fAnyOperationsAborted and not path.exists()


def _inside(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def delete_files(candidates: Iterable[Path], roots: Iterable[Path]) -> dict:
    """Delete the candidate files that are inside ``roots``.

    Returns {"deleted": [paths], "failed": [{"path", "reason"}]}. Candidates that
    do not exist are skipped silently (a partial file may never have been written).
    """
    real_roots = [Path(os.path.realpath(r)) for r in roots if r]
    deleted: list[str] = []
    failed: list[dict] = []
    seen: set[str] = set()
    for cand in candidates:
        if not cand:
            continue
        cand = Path(cand)
        key = os.path.normcase(str(cand))
        if key in seen:
            continue
        seen.add(key)
        if not os.path.lexists(cand):
            continue
        # resolve the folder, not the file, so a link at the file itself is caught below
        real = Path(os.path.realpath(cand.parent)) / cand.name
        if not _inside(real, real_roots):
            failed.append({"path": str(cand), "reason": "outside the download folder"})
            continue
        if cand.is_symlink() or not cand.is_file():
            failed.append({"path": str(cand), "reason": "not a regular file"})
            continue
        for attempt in range(6):  # a download that was just stopped may still hold the file
            try:
                if not (USE_RECYCLE_BIN and _recycle(real)):
                    real.unlink()
                deleted.append(str(real))
                break
            except PermissionError as exc:
                if attempt == 5:
                    failed.append({"path": str(cand), "reason": exc.strerror or str(exc)})
                else:
                    time.sleep(0.3)
            except OSError as exc:
                failed.append({"path": str(cand), "reason": exc.strerror or str(exc)})
                break
    return {"deleted": deleted, "failed": failed}
