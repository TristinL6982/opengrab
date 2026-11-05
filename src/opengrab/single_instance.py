"""
Single instance lock and URL forwarding for OpenGrab.
"""

import json
import os
import tempfile
import urllib.request
import urllib.error
from typing import Optional

if os.name == "nt":  # Windows
    import ctypes
    from ctypes import wintypes
    
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    
    # Error code for ERROR_ALREADY_EXISTS
    ERROR_ALREADY_EXISTS = 183
else:
    import fcntl


def acquire(name: str = "OpenGrab-SingleInstance") -> Optional[object]:
    """
    Acquire a single instance lock.
    
    Args:
        name: Lock name (default: "OpenGrab-SingleInstance")
        
    Returns:
        Handle to the lock object, or None if already exists
    """
    if os.name == "nt":  # Windows
        handle = kernel32.CreateMutexW(None, False, f"Local\\{name}")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return None
        return handle
    else:  # Unix-like systems
        lock_path = os.path.join(tempfile.gettempdir(), f"{name}.lock")
        fh = open(lock_path, "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            fh.close()
            return None
        return fh


def release(handle: Optional[object]) -> None:
    """
    Release a single instance lock.
    
    Args:
        handle: The lock handle returned by acquire(), or None
    """
    if handle is None:
        return
    
    if os.name == "nt":  # Windows
        kernel32.CloseHandle(handle)
    else:  # Unix-like systems
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, AttributeError, ValueError):
            pass
        try:
            handle.close()
        except (OSError, AttributeError):
            pass


def forward_to_running(urls: list[str], host: str, port: int, token: str, base_path: str = "",
                       files: Optional[list] = None) -> bool:
    """
    Forward URLs to a running OpenGrab instance.
    
    Args:
        urls: List of URLs to add
        host: Host of the target instance (e.g., "127.0.0.1")
        port: Port of the target instance 
        token: API bearer token for authentication
        base_path: Optional base path prefix
        
    Returns:
        True if all URLs were added successfully, False otherwise
    """
    # First check if server is alive by pinging it
    try:
        ping_url = f"http://{host}:{port}{base_path}/api/ping"
        req = urllib.request.Request(ping_url)
        req.add_header("Authorization", f"Bearer {token}")
        response = urllib.request.urlopen(req, timeout=3)
        if response.getcode() != 200:
            return False
    except Exception:
        return False
    
    # Now add the URLs (and, M14, files Windows handed to this launch)
    try:
        if files:
            req = urllib.request.Request(f"http://{host}:{port}{base_path}/api/open")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {token}")
            req.data = json.dumps({"targets": list(files)}).encode("utf-8")
            if urllib.request.urlopen(req, timeout=10).getcode() // 100 != 2:
                return False
        if not urls:
            return True
        api_url = f"http://{host}:{port}{base_path}/api/urls"
        data = {"urls": urls}
        req = urllib.request.Request(api_url)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {token}")
        req.data = json.dumps(data).encode("utf-8")
        
        response = urllib.request.urlopen(req, timeout=3)
        return response.getcode() // 100 == 2  # Check if it's a 2xx response
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Rendezvous: which OpenGrab copies are running, and where
#
# A second launch (Windows handing over a .torrent or magnet link) carries only
# the target. Each running copy lists itself in one per-user file so that launch
# can find the copy it should talk to. The file holds the pid, executable, data
# folder, host, port and base path, but never the API token: the token is read
# from that copy's own settings.json in its data folder (where it already lives).
# ---------------------------------------------------------------------------
import logging
import socket
import sys
import threading

_log = logging.getLogger("opengrab.single_instance")
_rv_lock = threading.Lock()


def rendezvous_path():
    """%LOCALAPPDATA%/OpenGrab/running.json (OPENGRAB_RENDEZVOUS_DIR overrides, for tests)."""
    from pathlib import Path
    override = os.environ.get("OPENGRAB_RENDEZVOUS_DIR")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "OpenGrab"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")) / "opengrab"
    return base / "running.json"


def norm_path(p) -> str:
    try:
        from pathlib import Path
        return os.path.normcase(str(Path(p).expanduser().resolve()))
    except Exception:
        return os.path.normcase(str(p))


def _read_entries() -> list:
    try:
        with open(rendezvous_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        items = data.get("instances", []) if isinstance(data, dict) else []
        return [e for e in items if isinstance(e, dict)]
    except (OSError, ValueError):
        return []


def _write_entries(entries: list) -> None:
    path = rendezvous_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"instances": entries}, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def port_open(host: str, port) -> bool:
    try:
        h = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
        with socket.create_connection((h, int(port)), timeout=1):
            return True
    except (OSError, ValueError, TypeError):
        return False


def entry_is_live(e: dict) -> bool:
    return pid_alive(e.get("pid")) and port_open(e.get("host", "127.0.0.1"), e.get("port"))


def register_instance(host: str, port: int, base_path: str, data_dir, exe: Optional[str] = None) -> None:
    """Record this running copy (replacing any stale entry for the same data folder or pid)."""
    entry = {"pid": os.getpid(), "exe": norm_path(exe or sys.executable), "data_dir": norm_path(data_dir),
             "host": host, "port": int(port), "base_path": base_path or ""}
    try:
        with _rv_lock:
            keep = [e for e in _read_entries()
                    if e.get("pid") != entry["pid"] and e.get("data_dir") != entry["data_dir"]
                    and pid_alive(e.get("pid"))]
            _write_entries(keep + [entry])
    except Exception:
        _log.warning("could not register in the running-instance list", exc_info=True)


def unregister_instance() -> None:
    try:
        with _rv_lock:
            keep = [e for e in _read_entries() if e.get("pid") != os.getpid()]
            _write_entries(keep)
    except Exception:
        _log.debug("could not remove the running-instance entry", exc_info=True)


def find_instance(data_dir=None, exe=None) -> Optional[dict]:
    """The live instance for a data folder, or else the one started from this executable.
    Entries whose process is gone or whose port is closed are ignored."""
    want_dir = norm_path(data_dir) if data_dir else None
    want_exe = norm_path(exe) if exe else None
    for e in reversed(_read_entries()):
        if want_dir is not None and e.get("data_dir") != want_dir:
            continue
        if want_dir is None and (want_exe is None or e.get("exe") != want_exe):
            continue
        if entry_is_live(e):
            return e
    return None


def read_token(data_dir) -> str:
    """The API token of the copy that owns data_dir (kept in its settings.json)."""
    from pathlib import Path
    try:
        data = json.loads((Path(data_dir) / "settings.json").read_text(encoding="utf-8"))
        return str(data.get("api_token") or "")
    except (OSError, ValueError):
        return ""


def forward_to_entry(entry: dict, urls: list, files: list) -> bool:
    token = read_token(entry.get("data_dir", ""))
    if not token:
        return False
    return forward_to_running(list(urls), entry.get("host", "127.0.0.1"), int(entry["port"]), token,
                              entry.get("base_path", ""), files=list(files))


def message_box(title: str, text: str) -> None:
    """Visible error for a windowed exe that has no console (MessageBoxW)."""
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
            return
        except Exception:
            _log.debug("MessageBoxW failed", exc_info=True)
    print(f"{title}: {text}", file=sys.stderr)


def report_forward_failure(count: int, reason: str) -> None:
    text = (f"OpenGrab could not hand {count} item(s) to the running window.\n\n{reason}\n\n"
            "Open OpenGrab and add the link or file there.")
    _log.error("forwarding failed: %s", reason)
    message_box("OpenGrab", text)
