"""M13 automation: event hooks, webhooks, Folder Watch, anti-standby and
"when done" actions. Pure helpers here; features/automation_api.py wires them
into the running app.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

EVENTS = ("finished", "failed", "all_done", "captcha", "added", "virus")
WHEN_DONE = ("nothing", "exit", "sleep", "hibernate", "shutdown")


# ------------------------------------------------------------------ hooks
@dataclass
class Hook:
    event: str
    command: str
    enabled: bool = True


def hooks_from(raw) -> tuple[list[Hook], list[str]]:
    hooks, errors = [], []
    for i, d in enumerate(raw or []):
        if not isinstance(d, dict):
            errors.append(f"hook {i + 1}: not an object")
            continue
        ev, cmd = str(d.get("event") or ""), str(d.get("command") or "").strip()
        if ev not in EVENTS:
            errors.append(f"hook {i + 1}: unknown event {ev!r}")
            continue
        if not cmd:
            errors.append(f"hook {i + 1}: empty command")
            continue
        hooks.append(Hook(ev, cmd, bool(d.get("enabled", True))))
    return hooks, errors


def event_env(event: str, data: dict) -> dict:
    """Event details as OPENGRAB_* environment variables (never shell text)."""
    env = {"OPENGRAB_EVENT": event}
    for key in ("id", "url", "title", "filename", "path", "status", "error", "size", "host", "kind"):
        if data.get(key) not in (None, ""):
            env["OPENGRAB_" + key.upper()] = str(data[key])[:4000]
    return env


def run_hook(hook: Hook, event: str, data: dict, runner=None, timeout: float = 300.0) -> dict:
    """Run one hook command with the event in its environment."""
    env = dict(os.environ, **event_env(event, data))
    argv = hook.command if os.name == "nt" else ["/bin/sh", "-c", hook.command]
    runner = runner or (lambda a, e, t: subprocess.run(
        a, env=e, timeout=t, capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    t0 = time.time()
    try:
        proc = runner(argv, env, timeout)
        return {"event": event, "command": hook.command, "code": proc.returncode,
                "output": ((proc.stdout or "") + (proc.stderr or ""))[-1000:], "ms": int((time.time() - t0) * 1000)}
    except Exception as exc:  # a broken hook must never disturb downloads
        return {"event": event, "command": hook.command, "code": None, "output": str(exc)[:500],
                "ms": int((time.time() - t0) * 1000)}


# ------------------------------------------------------------------ webhooks
@dataclass
class Webhook:
    url: str
    events: tuple = EVENTS
    secret: str = ""
    enabled: bool = True


def webhooks_from(raw) -> tuple[list[Webhook], list[str]]:
    out, errors = [], []
    for i, d in enumerate(raw or []):
        if not isinstance(d, dict):
            errors.append(f"webhook {i + 1}: not an object")
            continue
        url = str(d.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            errors.append(f"webhook {i + 1}: address must start with http:// or https://")
            continue
        events = tuple(e for e in (d.get("events") or EVENTS) if e in EVENTS) or EVENTS
        out.append(Webhook(url, events, str(d.get("secret") or ""), bool(d.get("enabled", True))))
    return out, errors


def webhook_body(event: str, data: dict, now: Optional[float] = None) -> bytes:
    payload = {"event": event, "time": now if now is not None else time.time(),
               "download": {k: data.get(k) for k in ("id", "url", "title", "filename", "path", "status",
                                                    "size", "error") if k in data}}
    if event in ("captcha", "all_done"):
        payload["details"] = {k: v for k, v in data.items() if k not in payload["download"]}
    return json.dumps(payload, default=str).encode("utf-8")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ Folder Watch
WATCH_EXTS = (".txt", ".url", ".crawljob", ".dlc", ".ccf", ".rsdf", ".torrent")
URL_RE = re.compile(r"(?:https?://|magnet:\?)[^\s<>\"']+", re.I)


@dataclass
class WatchJob:
    urls: list = field(default_factory=list)
    package: str = ""
    auto_start: Optional[bool] = None
    passwords: list = field(default_factory=list)
    torrent_file: str = ""
    container: tuple = ()  # (filename, bytes) for .dlc/.ccf/.rsdf


def parse_crawljob(text: str) -> list[WatchJob]:
    """JDownloader 2 Folder Watch .crawljob: key=value blocks (or JSON list)."""
    text = text.strip()
    jobs: list[WatchJob] = []
    if text.startswith("["):
        try:
            blocks = json.loads(text)
        except ValueError:
            blocks = []
    else:
        blocks, cur = [], {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                if not line and cur:
                    blocks.append(cur)
                    cur = {}
                continue
            key, sep, value = line.partition("=")
            if not sep:
                continue
            if key.strip().lower() == "text" and "text" in {k.lower() for k in cur}:
                blocks.append(cur)
                cur = {}
            cur[key.strip()] = value.strip()
        if cur:
            blocks.append(cur)
    for b in blocks:
        if not isinstance(b, dict):
            continue
        low = {str(k).lower(): v for k, v in b.items()}
        urls = URL_RE.findall(str(low.get("text", "")))
        if not urls:
            continue
        auto = low.get("autostart")
        pw = low.get("extractpasswords") or []
        if isinstance(pw, str):
            pw = [p.strip() for p in re.split(r"[,\n]", pw.strip("[]")) if p.strip().strip('"')]
            pw = [p.strip('"') for p in pw]
        jobs.append(WatchJob(urls=urls, package=str(low.get("packagename", "") or ""),
                             auto_start=None if auto is None else str(auto).upper() in ("TRUE", "1", "YES"),
                             passwords=list(pw)))
    return jobs


def parse_watch_file(path: Path) -> list[WatchJob]:
    ext = path.suffix.lower()
    if ext == ".torrent":
        return [WatchJob(torrent_file=str(path))]
    if ext in (".dlc", ".ccf", ".rsdf"):
        return [WatchJob(container=(path.name, path.read_bytes()))]
    text = path.read_text(encoding="utf-8", errors="replace")
    if ext == ".crawljob":
        return parse_crawljob(text)
    if ext == ".url":  # Windows Internet Shortcut
        m = re.search(r"^URL=(.+)$", text, re.M)
        return [WatchJob(urls=[m.group(1).strip()])] if m else []
    urls = URL_RE.findall(text)
    return [WatchJob(urls=urls)] if urls else []


def scan_folder(folder: Path, seen: dict, settle_s: float = 2.0, now: Optional[float] = None) -> list[Path]:
    """New watch files that stopped changing for settle_s seconds."""
    now = time.time() if now is None else now
    ready = []
    if not folder.is_dir():
        return ready
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.suffix.lower() not in WATCH_EXTS:
            continue
        st = p.stat()
        sig = (st.st_size, st.st_mtime)
        prev = seen.get(str(p))
        if prev != sig:
            seen[str(p)] = sig
            if now - st.st_mtime < settle_s:
                continue
        if now - st.st_mtime >= settle_s:
            ready.append(p)
    return ready


def archive_watch_file(path: Path) -> Path:
    done = path.parent / "added"
    done.mkdir(exist_ok=True)
    target = done / path.name
    i = 1
    while target.exists():
        target = done / f"{path.stem}_{i}{path.suffix}"
        i += 1
    os.replace(path, target)
    return target


# ------------------------------------------------------------------ anti-standby
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040


class StayAwake:
    """Keeps Windows from sleeping while held (SetThreadExecutionState).

    The flag belongs to the thread that set it, so one daemon thread owns it.
    On other systems this records the wish only.
    """

    def __init__(self, setter: Optional[Callable[[int], int]] = None):
        self._setter = setter
        self.active = False
        self._want = False
        self._lock = __import__("threading").Lock()
        self._thread = None
        self._wake = __import__("threading").Event()

    def _set(self, flags: int) -> int:
        if self._setter is not None:
            return self._setter(flags)
        if sys.platform != "win32":
            return 0
        import ctypes
        return ctypes.windll.kernel32.SetThreadExecutionState(flags)

    def hold(self, want: bool) -> None:
        with self._lock:
            if want == self._want:
                return
            self._want = want
            if self._thread is None:
                import threading
                self._thread = threading.Thread(target=self._run, name="stay-awake", daemon=True)
                self._thread.start()
        self._wake.set()

    def _run(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            want = self._want
            if want and not self.active:
                self._set(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
                self.active = True
            elif not want and self.active:
                self._set(ES_CONTINUOUS)
                self.active = False


# ------------------------------------------------------------------ when done
def when_done_command(action: str) -> Optional[list]:
    """The OS command for a "when done" action (None: nothing or handled in-app)."""
    if action not in WHEN_DONE or action in ("nothing", "exit"):
        return None
    if sys.platform == "win32":
        return {"shutdown": ["shutdown", "/s", "/t", "0"],
                "hibernate": ["shutdown", "/h"],
                "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]}[action]
    if sys.platform == "darwin":
        return {"shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'],
                "sleep": ["pmset", "sleepnow"], "hibernate": ["pmset", "sleepnow"]}[action]
    return {"shutdown": ["systemctl", "poweroff"], "sleep": ["systemctl", "suspend"],
            "hibernate": ["systemctl", "hibernate"]}[action]
