"""Rotating log file for OpenGrab."""

import logging
import logging.handlers
import os
import re
import sys
import threading
from pathlib import Path

from .settings import app_data_dir

_hooks_installed = False


# "scheme://user:password@host": links such as ftp://user:pass@host/file carry
# a password; the log file only keeps the user name
_URL_PASSWORD = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]*:)[^/\s@]+@")


def redact(text: str) -> str:
    return _URL_PASSWORD.sub(r"\1***@", text)


class RedactingFormatter(logging.Formatter):
    """Formatter that hides passwords embedded in URLs (also in tracebacks)."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def logs_dir() -> Path:
    """app_data_dir()/logs, created if missing."""
    d = Path(app_data_dir()) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _install_hooks() -> None:
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True
    prev_sys = sys.excepthook
    prev_thread = threading.excepthook

    def sys_hook(exc_type, exc_value, exc_tb):
        try:
            logging.getLogger("opengrab").error(
                "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
        except Exception:
            pass
        prev_sys(exc_type, exc_value, exc_tb)

    def thread_hook(args):
        try:
            logging.getLogger("opengrab").error(
                "Uncaught exception in thread %s", getattr(args.thread, "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        except Exception:
            pass
        prev_thread(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


def setup_logging(level: int = logging.INFO) -> Path:
    """Add one rotating file handler to the root logger and return the log file path.

    OPENGRAB_LOG_LEVEL (DEBUG, INFO, WARNING, ...) overrides ``level``."""
    level = logging.getLevelNamesMapping().get(
        os.environ.get("OPENGRAB_LOG_LEVEL", "").strip().upper(), level)
    log_file = logs_dir() / "opengrab.log"
    root = logging.getLogger()
    target = str(log_file.resolve())
    exists = any(
        str(getattr(h, "baseFilename", "")) and
        str(Path(getattr(h, "baseFilename", "")).resolve()) == target
        for h in root.handlers if getattr(h, "baseFilename", None)
    )
    if not exists:
        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
    _install_hooks()
    return log_file
