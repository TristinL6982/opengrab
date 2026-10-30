"""Notifications (M11f): download notification text plus an in-app notification center with history."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

KINDS = ("finished", "failed", "info")


@dataclass
class Notification:
    id: int
    kind: str
    title: str
    message: str
    time: float
    read: bool = False


def download_notification(name: str, status: str, error: str = "") -> tuple[str, str, str]:
    """(kind, title, message) for a download that finished or failed."""
    label = (name or "").strip() or "download"
    state = (status or "").strip().lower()
    if state in ("finished", "done", "completed"):
        return "finished", "Download finished", label
    if state in ("failed", "error"):
        reason = (error or "").strip()
        return "failed", "Download failed", f"{label}: {reason}" if reason else label
    raise ValueError(f"unsupported status: {status}")


def captcha_waiting_notification(host: str, kind: str) -> tuple[str, str, str]:
    """(kind, title, message) for a captcha that needs a human, pushed to
    every paired device so it can be solved remotely."""
    host = host or "unknown host"
    kind = kind or "captcha"
    return ("info", f"Captcha waiting: {host}",
            f"A {kind} captcha needs solving. Open OpenGrab on any paired device to solve it.")


class NotificationCenter:
    def __init__(self, max_items: int = 100, clock=time.time, toaster=None, toasts_enabled: bool = True) -> None:
        if max_items <= 0:
            raise ValueError("bad max_items")
        self.max_items = max_items
        self.clock = clock
        self.toaster = toaster
        self.toasts_enabled = toasts_enabled
        self._items: list[Notification] = []
        self._next_id = 1

    def add(self, kind: str, title: str, message: str = "") -> Notification:
        if kind not in KINDS:
            raise ValueError(f"unsupported kind: {kind}")
        note = Notification(self._next_id, kind, title, message, float(self.clock()))
        self._next_id += 1
        self._items.append(note)
        if len(self._items) > self.max_items:
            self._items = self._items[-self.max_items:]
        if self.toasts_enabled and self.toaster is not None:
            try:
                self.toaster(title, message)
            except Exception:
                pass
        return note

    def notify_download(self, name: str, status: str, error: str = "") -> Notification:
        return self.add(*download_notification(name, status, error))

    def items(self) -> list[Notification]:
        return list(reversed(self._items))

    def unread_count(self) -> int:
        return sum(1 for n in self._items if not n.read)

    def mark_read(self, note_id: int) -> bool:
        for n in self._items:
            if n.id == note_id:
                n.read = True
                return True
        return False

    def mark_all_read(self) -> None:
        for n in self._items:
            n.read = True

    def clear(self) -> None:
        self._items = []

    def to_list(self) -> list[dict]:
        return [asdict(n) for n in self._items]

    def load_list(self, data: list[dict]) -> None:
        items = [Notification(int(d["id"]), str(d["kind"]), str(d["title"]), str(d.get("message", "")),
                              float(d["time"]), bool(d.get("read", False))) for d in (data or [])]
        self._items = items[-self.max_items:]
        self._next_id = max((n.id for n in items), default=0) + 1