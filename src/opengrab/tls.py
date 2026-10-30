"""TLS trust for OpenGrab's own HTTPS requests made with urllib.

Python's default context on Windows uses the Windows certificate store,
which can lack an issuer until Windows fetches it (seen 2026-10-30:
api.github.com failed intermittently with "unable to get issuer
certificate"). The context below trusts both the Windows store (company
proxies and local roots keep working) and certifi's Mozilla bundle.
"""
from __future__ import annotations

import ssl
import urllib.request
from functools import lru_cache


@lru_cache(maxsize=1)
def context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError, ssl.SSLError):
        pass
    return ctx


def urlopen(req, timeout: float = 30):
    """urllib.request.urlopen with OpenGrab's trust settings."""
    return urllib.request.urlopen(req, timeout=timeout, context=context())
