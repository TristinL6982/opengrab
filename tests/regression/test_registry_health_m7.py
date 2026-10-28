from opengrab.plugins.registry import PluginRegistry


def _builtin_id(r):
    return next((pid for pid, p in r.plugins.items() if p.origin == "builtin"), None)


def test_fresh():
    r = PluginRegistry()
    r.scan(force=True)
    bid = _builtin_id(r)
    assert bid is not None
    assert r.health_for(bid) == {"ok": 0, "fail": 0, "last_error": None, "last_used": None}


def test_two_then_one():
    r = PluginRegistry()
    r.scan(force=True)
    bid = _builtin_id(r)
    assert bid is not None
    
    # Record two successes and one failure
    r.record_result(bid, True)
    r.record_result(bid, True)
    r.record_result(bid, False, "boom")
    
    h = r.health_for(bid)
    assert h["ok"] == 2
    assert h["fail"] == 1
    assert h["last_error"] == "boom"
    assert isinstance(h["last_used"], float)


def test_unknown_error():
    r = PluginRegistry()
    r.scan(force=True)
    bid = _builtin_id(r)
    assert bid is not None
    
    r.record_result(bid, False)
    h = r.health_for(bid)
    assert h["last_error"] == "unknown error"


def test_error_truncate():
    r = PluginRegistry()
    r.scan(force=True)
    bid = _builtin_id(r)
    assert bid is not None
    
    r.record_result(bid, False, "x" * 600)
    h = r.health_for(bid)
    assert len(h["last_error"]) == 500


def test_returns_copy():
    r = PluginRegistry()
    r.scan(force=True)
    bid = _builtin_id(r)
    assert bid is not None
    
    h = r.health_for(bid)
    h["ok"] = 99
    assert r.health_for(bid)["ok"] == 0


def test_snapshot_includes_health():
    r = PluginRegistry()
    r.scan(force=True)
    bid = _builtin_id(r)
    assert bid is not None
    
    r.record_result(bid, True)
    entry = [e for e in r.snapshot() if e["id"] == bid][0]
    assert entry["health"]["ok"] == 1
    assert "origin" in entry


def test_survives_rescan():
    r = PluginRegistry()
    r.scan(force=True)
    bid = _builtin_id(r)
    assert bid is not None
    
    r.record_result(bid, True)
    r.scan(force=True)
    assert r.health_for(bid)["ok"] == 1