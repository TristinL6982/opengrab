"""Logging setup for OpenGrab."""

import logging
import sys
import threading
from pathlib import Path

from .settings import app_data_dir


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
    
    # Check if handler is already added
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler) and handler.baseFilename == str(log_file):
            return log_file
    
    # Create rotating file handler
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    
    # Add handler to root logger
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
    
    # Install custom exception hooks
    old_excepthook = sys.excepthook
    
    def new_excepthook(exc_type, exc_value, exc_traceback):
        logging.exception("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
        old_excepthook(exc_type, exc_value, exc_traceback)
    
    sys.excepthook = new_excepthook
    
    # Install threading exception hook
    old_threading_excepthook = threading.excepthook
    
    def new_threading_excepthook(args):
        logging.exception("Uncaught exception in thread", exc_info=args)
        old_threading_excepthook(args)
    
    threading.excepthook = new_threading_excepthook
    
    return log_file
