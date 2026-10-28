"""Logging setup for OpenGrab."""

import logging
import sys
import threading
from pathlib import Path

from .settings import app_data_dir

# Import handlers explicitly to avoid AttributeError
import logging.handlers


def logs_dir() -> Path:
    """Get the logs directory, creating it if it doesn't exist."""
    log_dir = Path(app_data_dir()) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def setup_logging(level: int = logging.INFO) -> Path:
    """Set up rotating file logging.
    
    Returns the path to the log file.
    """
    log_file = logs_dir() / "opengrab.log"

    # Check if handler is already added, safely avoiding problematic handlers in test environments
    root_logger = logging.getLogger()
    
    for handler in root_logger.handlers:
        # Safely check each handler - specifically avoid issues with pytest's LiveLoggingNullHandler
        try:
            if hasattr(handler, 'baseFilename') and handler.baseFilename is not None:
                # Only proceed with the comparison if the handler is a rotating file handler
                # (We're using isinstance for test purposes)
                if str(handler.baseFilename) == str(log_file):
                    return log_file
        except Exception:
            # Skip any handlers that misbehave in this test environment
            continue

    # Create rotating file handler only if not already present
    try:
        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        handler.setFormatter(formatter)
        
        # Add handler to root logger
        root_logger.addHandler(handler)
        root_logger.setLevel(level)
    except Exception:
        # Silently fail if we can't set up logging - don't break the app for this
        pass
    
    # Install custom exception hooks with fallbacks
    old_excepthook = sys.excepthook
    
    def new_excepthook(exc_type, exc_value, exc_traceback):
        try:
            logging.exception("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
        except Exception:
            # Fallback to stderr
            print(f"Uncaught exception: {exc_type.__name__}: {exc_value}", file=sys.stderr)
        old_excepthook(exc_type, exc_value, exc_traceback)
    
    sys.excepthook = new_excepthook
    
    # Install threading exception hook
    old_threading_excepthook = threading.excepthook
    
    def new_threading_excepthook(args):
        try:
            logging.exception("Uncaught exception in thread", exc_info=args)
        except Exception:
            # Fallback to stderr
            print(f"Uncaught exception in thread: {args[0].__name__}: {args[1]}", file=sys.stderr)
        old_threading_excepthook(args)
    
    threading.excepthook = new_threading_excepthook
    
    return log_file
