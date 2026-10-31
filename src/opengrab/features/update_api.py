"""M14 routes: check for, install and roll back updates.

GET  /api/update             current version, newest release, rollback state
POST /api/update/install     download, verify, self-test, then restart into it
POST /api/update/rollback    go back to the version before the last update

Installing and rolling back replace program files, so they need the master
token from this computer. Checking contacts api.github.com only when the
user asks or ``update_check`` is on (once a day, from the UI).
"""
from __future__ import annotations

import json
import os
import threading
import time

from .. import updater
from .system_api import _local


def setup(srv):
    r = srv.router
    state = {"checked": 0.0, "result": None, "busy": ""}

    def _exit_soon():
        def go():
            time.sleep(1.5)  # let the HTTP reply reach the UI
            auto = srv.features.get("automation")
            if auto is not None and auto.exit_app is not None:
                auto.exit_app()
                return
            try:
                srv.stop()
                srv.manager.shutdown()
            finally:
                os._exit(0)
        threading.Thread(target=go, name="opengrab-update-exit", daemon=True).start()

    def status(req):
        out = {"current": updater.__version__, "frozen": updater.is_frozen(),
               "rollback": updater.is_frozen() and updater.rollback_available(),
               "signing_ready": bool(updater.RELEASE_KEYS), "busy": state["busy"]}
        if req.q("check") == "1" or (state["result"] and time.time() - state["checked"] < 3600):
            if req.q("check") == "1":
                try:
                    state["result"], state["checked"] = updater.check(), time.time()
                except Exception as exc:  # offline, rate limited, ...
                    return {**out, "error": f"could not check for updates: {exc}"[:300]}
            out.update(state["result"] or {})
        return out

    def install(req):
        if not _local(req):
            return 403, {"error": "install updates from this computer"}
        if not updater.is_frozen():
            return 400, {"error": "this copy runs from source; update it with git instead"}
        if not updater.RELEASE_KEYS:
            return 400, {"error": "this build has no release signing key, so updates cannot be verified"}
        if state["busy"]:
            return 409, {"error": f"an update is already being installed ({state['busy']})"}
        state["busy"] = "downloading"
        try:
            data = json.loads(updater._get(updater.API))
            rel = updater.parse_release(data)
            if rel is None or not updater.is_newer(rel.version):
                return 400, {"error": "no newer release is available"}
            zip_bytes = updater._get(rel.zip_url, timeout=600)
            sig = updater._get(rel.sig_url).decode("ascii", "replace")
            state["busy"] = "testing"
            root = updater.install_dir()
            new = updater.stage(zip_bytes, sig, updater.staging_dir())
            if not updater.writable(updater.install_dir().parent):
                state["busy"] = "waiting for you to allow it in the Windows administrator prompt"
            updater.launch_swap(root, new, root.with_name(root.name + ".old"))
        except updater.UpdateError as exc:
            return 400, {"error": str(exc)}
        except Exception as exc:
            return 502, {"error": f"update failed: {exc}"[:300]}
        finally:
            state["busy"] = ""
        _exit_soon()
        return {"ok": True, "version": rel.version, "restarting": True}

    def rollback(req):
        if not _local(req):
            return 403, {"error": "roll back from this computer"}
        if not (updater.is_frozen() and updater.rollback_available()):
            return 400, {"error": "there is no previous version to go back to"}
        root = updater.install_dir()
        updater.launch_swap(root, root.with_name(root.name + ".old"), root.with_name(root.name + ".new"))
        _exit_soon()
        return {"ok": True, "restarting": True}

    r.get(r"/api/update", status)
    r.post(r"/api/update/install", install, action="settings_write")
    r.post(r"/api/update/rollback", rollback, action="settings_write")
    return None
