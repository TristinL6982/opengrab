"""Features wired into the running app after the 2026-10-30 integration audit.

Each module exposes ``setup(srv)``; it may register routes on ``srv.router``,
hook ``srv.manager`` and return an object kept in ``srv.features[name]``.
A feature that fails to start is logged and skipped, so one broken optional
component never stops the API server.
"""
from __future__ import annotations

import logging

from . import addons_api, captcha, notify

log = logging.getLogger("opengrab.features")

# (name, module) in start order: later features may use earlier ones.
# Modules are imported statically so tools/import_audit.py can see them.
FEATURES = (
    ("notify", notify),
    ("captcha", captcha),
    ("addons", addons_api),
)


def install_all(srv) -> dict:
    installed: dict = {}
    srv.features = installed
    for name, mod in FEATURES:
        try:
            installed[name] = mod.setup(srv)
        except Exception:
            log.exception("feature %s failed to start", name)
    return installed
