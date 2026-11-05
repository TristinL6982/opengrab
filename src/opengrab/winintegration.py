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

# Registered Applications: the supported way to be offered in Settings > Default apps.
# Windows keeps the user's pick in ...\UserChoice (protected by a hash). OpenGrab
# only ever reads it; it never writes or deletes it.
APP_NAME = "OpenGrab"
CAP_KEY = r"Software\OpenGrab\Capabilities"
REGISTERED_APPS = r"Software\RegisteredApplications"
PROGID_MAGNET = "OpenGrab.magnet"
PROGID_TORRENT = "OpenGrab.torrent"
URL_USERCHOICE = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\{}\UserChoice"
EXT_USERCHOICE = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{}\UserChoice"


# Set by the command line: the --data-dir/--port/--download-dir this copy was started with.
STARTUP_OPTIONS: dict = {"data_dir": None, "port": None, "download_dir": None}


def launch_command() -> str:
    """How Windows should start OpenGrab (frozen exe or this Python), with the
    data folder, port and download folder this copy was started with, so a copy
    run on its own data (a test or portable copy) opens links in itself."""
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}"'
    else:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        exe = pyw if pyw.exists() else Path(sys.executable)
        cmd = f'"{exe}" -m opengrab'
    opts = STARTUP_OPTIONS
    if opts.get("data_dir"):
        cmd += f' --data-dir "{opts["data_dir"]}"'
    if opts.get("port"):
        cmd += f' --port {int(opts["port"])}'
    if opts.get("download_dir"):
        cmd += f' --download-dir "{opts["download_dir"]}"'
    return cmd


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
            if scheme == "magnet":
                self._sync_capabilities()
            return
        self._set(key, "", f"URL:{scheme} protocol")
        self._set(key, "URL Protocol", "")
        self._set(f"{key}\\shell\\open\\command", "", f'{self.command} "%1"')
        if scheme == "magnet":
            self.register_capabilities()

    def protocol_is_ours(self, scheme: str) -> bool:
        return (self._get(f"{CLASSES}\\{scheme}\\shell\\open\\command") or "").startswith(self.command)

    # -- file types -------------------------------------------------------------
    def progid_for(self, ext: str) -> str:
        """The ProgId an extension points at: .torrent uses the same ProgId the
        Default apps Capabilities name; the other file types share OpenGrab.Download."""
        return PROGID_TORRENT if ext == ".torrent" else PROGID

    def _progid_is_ours(self, progid: str) -> bool:
        return bool(progid) and (self._get(f"{CLASSES}\\{progid}\\shell\\open\\command") or "").startswith(self.command)

    def set_file_type(self, ext: str, on: bool) -> None:
        progid = self.progid_for(ext)
        if on:
            if ext == ".torrent":
                self.register_capabilities()  # writes the OpenGrab.torrent ProgId and its command
            else:
                self._set(f"{CLASSES}\\{progid}", "", "OpenGrab download")
                self._set(f"{CLASSES}\\{progid}\\shell\\open\\command", "", f'{self.command} "%1"')
            self._set(f"{CLASSES}\\{ext}\\OpenWithProgids", progid, "")
            self._set(f"{CLASSES}\\{ext}", "", progid)
            return
        current = self._get(f"{CLASSES}\\{ext}") or ""
        if current in (progid, PROGID) and self._progid_is_ours(current):
            self._delete_value(f"{CLASSES}\\{ext}", "")
            for p in {progid, current}:
                self._delete_value(f"{CLASSES}\\{ext}\\OpenWithProgids", p)
            if ext == ".torrent":
                self._sync_capabilities()

    def file_type_is_ours(self, ext: str) -> bool:
        current = self._get(f"{CLASSES}\\{ext}") or ""
        # PROGID is also accepted for .torrent: the 0.1.3 installer used it
        return current in (self.progid_for(ext), PROGID) and self._progid_is_ours(current)

    # -- Default apps capabilities -------------------------------------------
    def exe_path(self) -> str:
        c = self.command.strip()
        return c[1:c.index('"', 1)] if c.startswith('"') and '"' in c[1:] else c.split(" ")[0]

    def register_capabilities(self) -> None:
        """Make OpenGrab selectable for magnet links and .torrent files in
        Settings > Default apps (HKCU only). Does not change anyone's choice."""
        exe = self.exe_path()
        cmd = f'{self.command} "%1"'
        self._set(CAP_KEY, "ApplicationName", APP_NAME)
        self._set(CAP_KEY, "ApplicationDescription", "Download manager for links, files and torrents")
        self._set(CAP_KEY, "ApplicationIcon", f"{exe},0")
        self._set(CAP_KEY + r"\UrlAssociations", "magnet", PROGID_MAGNET)
        self._set(CAP_KEY + r"\FileAssociations", ".torrent", PROGID_TORRENT)
        self._set(REGISTERED_APPS, APP_NAME, CAP_KEY)
        for progid, label in ((PROGID_MAGNET, "URL:magnet protocol"), (PROGID_TORRENT, "OpenGrab torrent file")):
            key = f"{CLASSES}\\{progid}"
            self._set(key, "", label)
            if progid == PROGID_MAGNET:
                self._set(key, "URL Protocol", "")
            self._set(key + r"\DefaultIcon", "", f"{exe},0")
            self._set(key + r"\shell\open\command", "", cmd)
        self._notify()

    def capabilities_registered(self) -> bool:
        return self._get(REGISTERED_APPS, APP_NAME) == CAP_KEY and self._get(CAP_KEY, "ApplicationName") is not None

    def unregister_capabilities(self) -> None:
        """Remove what register_capabilities wrote. UserChoice is never touched."""
        self._delete_tree(r"Software\OpenGrab")
        self._delete_value(REGISTERED_APPS, APP_NAME)
        self._delete_tree(f"{CLASSES}\\{PROGID_MAGNET}")
        self._delete_tree(f"{CLASSES}\\{PROGID_TORRENT}")
        self._notify()

    def _sync_capabilities(self) -> None:
        """Drop the capabilities once neither magnet nor .torrent is ours any more."""
        if self.protocol_is_ours("magnet") or self.file_type_is_ours(".torrent"):
            return
        cmd = self._get(f"{CLASSES}\\{PROGID_MAGNET}\\shell\\open\\command") or ""
        if cmd.startswith(self.command) or not self.capabilities_registered():
            self.unregister_capabilities()

    def _notify(self) -> None:
        if self.base:  # a scratch key in tests must not poke the shell
            return
        try:
            import ctypes
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
        except Exception:  # noqa: BLE001 - cosmetic refresh only
            pass

    # -- who handles it now (read only) ------------------------------------------
    def user_choice(self, scheme_or_ext: str) -> str:
        sub = (EXT_USERCHOICE if scheme_or_ext.startswith(".") else URL_USERCHOICE).format(scheme_or_ext)
        return self._get(sub, "ProgId") or ""

    def _class_value(self, progid: str, sub: str, name: str = "") -> Optional[str]:
        v = self._get(f"{CLASSES}\\{progid}{sub}", name)
        if v is None and not self.base:
            try:
                with self.w.OpenKey(self.w.HKEY_LOCAL_MACHINE, f"{CLASSES}\\{progid}{sub}") as k:
                    v = self.w.QueryValueEx(k, name)[0]
            except OSError:
                v = None
        return v

    def describe_progid(self, progid: str) -> str:
        """Friendly name: Application\\ApplicationName, else the exe's
        description or file name, else the ProgId itself."""
        name = self._class_value(progid, r"\Application", "ApplicationName")
        if name:
            return _resource_string(name)
        cmd = self._class_value(progid, r"\shell\open\command") or ""
        return (_exe_name(cmd) if cmd else "") or progid

    def current_handler(self, scheme_or_ext: str) -> dict:
        progid = self.user_choice(scheme_or_ext)
        if progid:
            return {"progid": progid, "name": self.describe_progid(progid),
                    "is_ours": progid.lower().startswith("opengrab.")}
        # no choice made yet: whatever is registered as the class default
        is_scheme = not scheme_or_ext.startswith(".")
        ours = self.protocol_is_ours(scheme_or_ext) if is_scheme else self.file_type_is_ours(scheme_or_ext)
        if ours:
            name = APP_NAME
        elif is_scheme:
            cmd = self._class_value(scheme_or_ext, r"\shell\open\command") or ""
            name = _exe_name(cmd) if cmd else ""
        else:
            dflt = self._class_value(scheme_or_ext, "") or ""
            name = self.describe_progid(dflt) if dflt else ""
        return {"progid": "", "name": name, "is_ours": bool(ours)}

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


def _exe_name(command: str) -> str:
    """Friendly name from a shell command: the exe's file description, else its name."""
    c = command.strip()
    exe = c[1:c.index('"', 1)] if c.startswith('"') and '"' in c[1:] else c.split(" ")[0]
    if not exe:
        return ""
    return _file_description(exe) or Path(exe).stem


def _file_description(path: str) -> str:
    if sys.platform != "win32" or not Path(path).is_file():
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        v = ctypes.WinDLL("version", use_last_error=True)
        v.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
        v.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        v.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                     ctypes.POINTER(wintypes.UINT)]
        size = v.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not v.GetFileVersionInfoW(path, 0, size, buf):
            return ""
        ptr, n = ctypes.c_void_p(), wintypes.UINT()
        if not v.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(n)) or n.value < 4:
            return ""
        lang, cp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
        if v.VerQueryValueW(buf, f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription",
                            ctypes.byref(ptr), ctypes.byref(n)) and n.value:
            return ctypes.wstring_at(ptr.value, n.value).rstrip("\0").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def _resource_string(text: str) -> str:
    """Resolve "@{...}" / "@dll,-123" indirect strings (used by app ProgIds)."""
    if not text.startswith("@") or sys.platform != "win32":
        return text
    try:
        import ctypes
        out = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shlwapi.SHLoadIndirectString(text, out, 260, None) == 0 and out.value:
            return out.value
    except Exception:  # noqa: BLE001
        pass
    return text


def current_handler(scheme_or_ext: str, reg: Optional["Registry"] = None) -> dict:
    """{"progid", "name", "is_ours"} for the user's choice for a scheme (magnet)
    or extension (.torrent). Read only."""
    return (reg or Registry()).current_handler(scheme_or_ext)


def open_default_apps() -> bool:
    """Open Settings > Default apps on OpenGrab's page (Windows 11), else the
    general Default apps page. The user makes the choice; nothing is written."""
    import os
    uris = []
    if sys.getwindowsversion().build >= 22000:  # type: ignore[attr-defined]
        uris.append(f"ms-settings:defaultapps?registeredAppUser={APP_NAME}")
    uris.append("ms-settings:defaultapps")
    for uri in uris:
        try:
            os.startfile(uri)  # type: ignore[attr-defined]
            return True
        except OSError:
            continue
    return False


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
