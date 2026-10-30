"""M14: Windows integration - link handlers, file associations, autostart.

Everything is per user (HKEY_CURRENT_USER), so no administrator rights are
needed and nothing touches other users. Each item can be switched on and off
from Settings (and later by the installer).

* protocols: magnet:, opengrab://  -> "OpenGrab.exe" "%1"
* files:     .torrent, .crawljob, .dlc -> "OpenGrab.exe" "%1"
* autostart: HKCU\\...\\Run "OpenGrab" -> "OpenGrab.exe" --minimized
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

CLASSES = r"Software\Classes"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PROTOCOLS = ("magnet", "opengrab")
FILE_TYPES = (".torrent", ".crawljob", ".dlc")
PROGID = "OpenGrab.Download"


def launch_command() -> str:
    """How Windows should start OpenGrab (frozen exe or this Python)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    return f'"{exe}" -m opengrab'


class Registry:
    """Thin winreg wrapper; ``base`` lets tests use a scratch subkey."""

    def __init__(self, base: str = "", command: Optional[str] = None):
        import winreg
        self.w = winreg
        self.base = base
        self.command = command or launch_command()

    def _path(self, sub: str) -> str:
        return f"{self.base}\\{sub}" if self.base else sub

    def _set(self, sub: str, name: str, value: str) -> None:
        with self.w.CreateKeyEx(self.w.HKEY_CURRENT_USER, self._path(sub), 0, self.w.KEY_WRITE) as k:
            self.w.SetValueEx(k, name, 0, self.w.REG_SZ, value)

    def _get(self, sub: str, name: str = "") -> Optional[str]:
        try:
            with self.w.OpenKey(self.w.HKEY_CURRENT_USER, self._path(sub)) as k:
                return self.w.QueryValueEx(k, name)[0]
        except OSError:
            return None

    def _delete_tree(self, sub: str) -> None:
        def rec(path):
            try:
                with self.w.OpenKey(self.w.HKEY_CURRENT_USER, path, 0, self.w.KEY_READ | self.w.KEY_WRITE) as k:
                    while True:
                        try:
                            child = self.w.EnumKey(k, 0)
                        except OSError:
                            break
                        rec(path + "\\" + child)
                self.w.DeleteKey(self.w.HKEY_CURRENT_USER, path)
            except FileNotFoundError:
                pass
        rec(self._path(sub))

    def _delete_value(self, sub: str, name: str) -> None:
        try:
            with self.w.OpenKey(self.w.HKEY_CURRENT_USER, self._path(sub), 0, self.w.KEY_WRITE) as k:
                self.w.DeleteValue(k, name)
        except FileNotFoundError:
            pass

    # -- protocols -----------------------------------------------------------
    def set_protocol(self, scheme: str, on: bool) -> None:
        key = f"{CLASSES}\\{scheme}"
        if not on:
            if (self._get(f"{key}\\shell\\open\\command") or "").startswith(self.command):
                self._delete_tree(key)  # only remove what OpenGrab registered
            return
        self._set(key, "", f"URL:{scheme} protocol")
        self._set(key, "URL Protocol", "")
        self._set(f"{key}\\shell\\open\\command", "", f'{self.command} "%1"')

    def protocol_is_ours(self, scheme: str) -> bool:
        return (self._get(f"{CLASSES}\\{scheme}\\shell\\open\\command") or "").startswith(self.command)

    # -- file types -------------------------------------------------------------
    def set_file_type(self, ext: str, on: bool) -> None:
        if on:
            self._set(f"{CLASSES}\\{PROGID}", "", "OpenGrab download")
            self._set(f"{CLASSES}\\{PROGID}\\shell\\open\\command", "", f'{self.command} "%1"')
            self._set(f"{CLASSES}\\{ext}\\OpenWithProgids", PROGID, "")
            self._set(f"{CLASSES}\\{ext}", "", PROGID)
        elif self._get(f"{CLASSES}\\{ext}") == PROGID:
            self._delete_value(f"{CLASSES}\\{ext}", "")
            self._delete_value(f"{CLASSES}\\{ext}\\OpenWithProgids", PROGID)

    def file_type_is_ours(self, ext: str) -> bool:
        return self._get(f"{CLASSES}\\{ext}") == PROGID

    # -- autostart --------------------------------------------------------------
    def set_autostart(self, on: bool) -> None:
        if on:
            self._set(RUN_KEY, "OpenGrab", f"{self.command} --minimized")
        else:
            self._delete_value(RUN_KEY, "OpenGrab")

    def autostart_is_on(self) -> bool:
        return bool(self._get(RUN_KEY, "OpenGrab"))

    def status(self) -> dict:
        return {"protocols": {p: self.protocol_is_ours(p) for p in PROTOCOLS},
                "files": {e: self.file_type_is_ours(e) for e in FILE_TYPES},
                "autostart": self.autostart_is_on(), "command": self.command}


def available() -> bool:
    return sys.platform == "win32"


def looks_like_url(text: str) -> bool:
    t = text.lower()
    return t.startswith(("http://", "https://", "ftp://", "magnet:?"))


def normalize_target(raw: str) -> str:
    """What Windows hands over ("%1") as a URL or a file path.

    opengrab://add?url=<encoded>, opengrab://https://host/x and
    opengrab:https://host/x all become the inner URL.
    """
    from urllib.parse import parse_qs, unquote, urlsplit
    t = (raw or "").strip().strip('"').strip()
    if not t.lower().startswith("opengrab:"):
        return t
    rest = t[len("opengrab:"):].lstrip("/")
    if rest.lower().startswith("add"):
        q = parse_qs(urlsplit("x://h/" + rest).query)
        inner = (q.get("url") or [""])[0]
        return inner if looks_like_url(inner) else ""
    inner = unquote(rest)
    for scheme in ("https:/", "http:/", "ftp:/"):  # a browser may fold "//" to "/"
        if inner.lower().startswith(scheme) and not inner.lower().startswith(scheme + "/"):
            inner = scheme + "/" + inner[len(scheme):]
    return inner if looks_like_url(inner) else ""


def split_targets(args) -> tuple[list, list]:
    """Command-line targets -> (urls, local file paths)."""
    urls, files = [], []
    for a in args or []:
        t = normalize_target(a)
        if not t:
            continue
        (urls if looks_like_url(t) else files).append(t if looks_like_url(t) else str(Path(t).resolve()))
    return urls, files
