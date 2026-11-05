"""Files dropped onto the app window: the page uploads their bytes (a web page never
learns a file's path) and this decides what to do with them by extension."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

MAX_IMPORT_BYTES = 32 * 1024 * 1024
TORRENT_EXT = ".torrent"
LINK_FILE_EXTS = (".dlc", ".ccf", ".rsdf", ".crawljob", ".txt", ".url")
IMPORT_EXTS = (TORRENT_EXT,) + LINK_FILE_EXTS


class ImportError_(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def safe_name(name: str) -> str:
    """Last path component only, control characters and path separators removed."""
    base = re.split(r"[\/]", str(name or ""))[-1]
    base = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", base).strip(" .")
    return base[:150]


def import_dropped_file(srv, name: str, data: bytes) -> dict:
    name = safe_name(name)
    ext = Path(name).suffix.lower()
    if not name or ext not in IMPORT_EXTS:
        shown = ext or "of this kind"
        raise ImportError_(415, f"OpenGrab can't open {shown} files")
    if len(data) > MAX_IMPORT_BYTES:
        raise ImportError_(413, "file is larger than 32 MB")
    mgr = srv.manager
    if ext == TORRENT_EXT:
        try:
            link = mgr.add_torrent_bytes(data, name, title=Path(name).stem)
        except ValueError as exc:
            raise ImportError_(400, str(exc)) from exc
        return {"ok": True, "name": name, "kind": "torrent", "links": 1, "id": link.id}
    auto = srv.features.get("automation")
    if auto is None:
        raise ImportError_(503, "link import is not running")
    # the bytes go to a private temp dir under a fixed name; the user's filename is never a path
    with tempfile.TemporaryDirectory(prefix="opengrab-import-") as tmp:
        target = Path(tmp) / ("dropped" + ext)
        target.write_bytes(data)
        try:
            count = auto._import(target, "linkgrabber")
        except Exception as exc:  # noqa: BLE001  DLC/CCF raise ContainerError
            raise ImportError_(400, f"{name}: {exc}") from exc
    return {"ok": True, "name": name, "kind": "links", "links": count}
