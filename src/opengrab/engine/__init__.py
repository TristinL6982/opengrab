"""Download engine: prioritized queue, resumable chunked downloads.

    DownloadManager   — owns links, workers, state machine, persistence
    LinkDownload      — one file: probe -> (parallel chunks | stream) -> finalize
"""
from .downloader import LinkDownload, DownloadError
from .manager import DownloadManager

__all__ = ["DownloadManager", "LinkDownload", "DownloadError"]
