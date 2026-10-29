import json

from opengrab import extrules as er


def test_defaults_and_normalize():
    r = er.normalize_rules(None)
    assert r["enabled"] is True and r["bypass_key"] == "alt"
    assert "zip" in r["file_types"] and r["min_size_bytes"] == 1024 * 1024
    r = er.normalize_rules({"file_types": [".ZIP", "zip", " Mp4 "], "min_size_bytes": "-5",
                            "sites": {"https://Example.com/x": False, "": True},
                            "bypass_key": "bogus", "enabled": 0})
    assert r["file_types"] == ["zip", "mp4"]
    assert r["min_size_bytes"] == 0
    assert r["sites"] == {"example.com": False}
    assert r["bypass_key"] == "alt" and r["enabled"] is False


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "sub" / "extrules.json"
    saved = er.save_rules(p, {"file_types": ["iso"], "sites": {"a.com": True}})
    assert er.load_rules(p) == saved
    assert json.loads(p.read_text(encoding="utf-8"))["file_types"] == ["iso"]
    assert er.load_rules(tmp_path / "missing.json") == er.normalize_rules(None)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert er.load_rules(bad)["enabled"] is True


def test_should_intercept_types_size_bypass():
    r = er.normalize_rules({"min_size_bytes": 100})
    assert er.should_intercept(r, "https://x.com/f/file.zip?t=1", size=500)
    assert not er.should_intercept(r, "https://x.com/page.html", size=500)
    assert er.should_intercept(r, "https://x.com/dl?id=3", filename="movie.MKV")
    assert not er.should_intercept(r, "https://x.com/a.zip", size=50)
    assert er.should_intercept(r, "https://x.com/a.zip", size=-1)
    assert not er.should_intercept(r, "https://x.com/a.zip", size=500, bypass_pressed=True)
    none_key = er.normalize_rules({"bypass_key": "none"})
    assert er.should_intercept(none_key, "https://x.com/a.zip", bypass_pressed=True)
    assert not er.should_intercept({"enabled": False}, "https://x.com/a.zip")
    any_type = er.normalize_rules({"file_types": [], "min_size_bytes": 0})
    assert er.should_intercept(any_type, "https://x.com/page.html")


def test_site_rules_most_specific_wins():
    r = er.normalize_rules({"sites": {"example.com": False, "dl.example.com": True}})
    assert not er.should_intercept(r, "https://www.example.com/a.zip")
    assert er.should_intercept(r, "https://dl.example.com/a.zip")
    assert not er.should_intercept(r, "https://cdn.other.net/a.zip", page_url="https://example.com/")
    assert not er.should_intercept(r, "https://notexample.com.evil/a.txt")
    assert er.site_enabled(r, "badexample.com") is None


def test_scope_cookies_and_public_config():
    cookies = [
        {"name": "s", "value": "1", "domain": ".example.com"},
        {"name": "t", "value": "2", "domain": "other.com"},
        {"name": "u", "value": "3"},
        {"value": "no-name"},
        "junk",
    ]
    kept = er.scope_cookies(cookies, "https://dl.example.com/f.zip")
    assert [c["name"] for c in kept] == ["s", "u"]
    assert kept[0]["domain"] == "example.com" and kept[0]["path"] == "/"
    assert er.scope_cookies(cookies, "not a url") == []
    cfg = er.public_config({"bypass_key": "shift"})
    assert cfg["version"] == 1 and cfg["rules"]["bypass_key"] == "shift"
