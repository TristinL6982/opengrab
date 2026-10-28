import json
import os
import pathlib
import tempfile

from opengrab.plugins.registry import PluginRegistry

T = {"id": "exhost", "name": "ExHost", "site": "exhost.com",
     "direct_url_regex": r'href="([^"]+)" id="dl"'}


def _registry():
    r = PluginRegistry()
    d = tempfile.mkdtemp()
    r.set_user_dir(pathlib.Path(d))
    return r, d


def _write(d, name, obj):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f)


def test_good_template_loads():
    r, d = _registry()
    _write(d, "good.json", T)
    r.scan(force=True)
    assert r.get("exhost") is not None
    assert r.get("exhost").origin == "template"
    assert any(p.id == "exhost" for p in r.for_url("https://exhost.com/f/abc")) is True


def test_bad_json_records_error():
    r, d = _registry()
    _write(d, "bad.json", "{nope")
    r.scan(force=True)
    assert "template:bad.json" in r.errors


def test_removed_template_disappears():
    r, d = _registry()
    _write(d, "good.json", T)
    r.scan(force=True)
    assert r.get("exhost") is not None
    os.remove(os.path.join(d, "good.json"))
    r.scan(force=True)
    assert r.get("exhost") is None


def test_fixed_template_clears_error():
    r, d = _registry()
    _write(d, "bad.json", "{nope")
    r.scan(force=True)
    assert "template:bad.json" in r.errors
    _write(d, "bad.json", T)
    r.scan(force=True)
    assert "template:bad.json" not in r.errors
    assert r.get("exhost") is not None


def test_template_cannot_override_builtin():
    fresh = PluginRegistry()
    fresh.scan(force=True)
    builtin_ids = [k for k, p in fresh.plugins.items() if p.origin == "builtin"]
    assert len(builtin_ids) > 0
    bid = builtin_ids[0]
    r, d = _registry()
    _write(d, "conflict.json", dict(T, id=bid, site="conflict.example"))
    r.scan(force=True)
    assert r.get(bid).origin == "builtin"
    assert f"template:{bid}" in r.errors
