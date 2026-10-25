"""OpenGrab — a modern, unpaywalled grabber/downloader.

Architecturally inspired by JDownloader 2 (grabber → container → queue →
downloader, per-site plugins, captcha service, accounts, filters) but
reimplemented from scratch in Python with a modern web UI, pluggable
captcha solvers (no service paywall), and TLS-fingerprint anti-bot
handling via curl_cffi.
"""

__version__ = "0.1.0"
APP_NAME = "OpenGrab"
APP_ID = "opengrab"
