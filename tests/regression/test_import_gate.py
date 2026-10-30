"""Integration gate: every product module must be reachable from a runtime
entry point (app, server, cli, smoke, __main__).

KNOWN_ORPHANS lists the modules found unreachable by the 2026-10-30 audit
(docs/plan/INTEGRATION_AUDIT.md). It may only shrink: a new module that no
entry point imports fails this test, and a listed module that has been wired
in must be removed from the list.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

KNOWN_ORPHANS = {
    "accountauth", "archives", "avscan", 
    "categories", "cnl", "cnl_server", "cookies", "debrid",
    "hashverify", "hostlimits", "linkcheck", "netconfig",
    "plugins.marketplace", "plugins.permissions", "plugins.sandbox",
    "plugins.sdk", "plugins.updates", "portmap", "premium", "proxies",
    "queues", "reconnect", "recovery", "scheduler", "segments", "siteauth",
    "speedprofiles", "spider", "torrent", "trackers", "usage",
}


def _audit():
    spec = importlib.util.spec_from_file_location("import_audit", ROOT / "tools" / "import_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.audit(ROOT)


def test_no_new_unreachable_modules():
    unreachable = set(_audit()["unreachable"])
    new = sorted(unreachable - KNOWN_ORPHANS)
    assert not new, f"modules not imported by any entry point: {new}"


def test_known_orphan_list_only_shrinks():
    unreachable = set(_audit()["unreachable"])
    wired = sorted(KNOWN_ORPHANS - unreachable)
    assert not wired, f"now reachable, remove from KNOWN_ORPHANS: {wired}"
