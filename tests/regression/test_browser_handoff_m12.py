"""M12g: browser referrer, user agent and cookies reach the downloader."""
from pathlib import Path

from opengrab.extrules import browser_request_extras

ROOT = Path(__file__).resolve().parents[2]


def test_no_browser_info_gives_nothing():
    assert browser_request_extras({}, "https://a.com/f.zip") == ({}, {})
    assert browser_request_extras(None, "https://a.com/f.zip") == ({}, {})
    assert browser_request_extras({"browser": "x"}, "https://a.com/f.zip") == ({}, {})


def test_headers_and_scoped_cookies():
    extra = {"browser": {
        "referrer": "https://a.com/page",
        "user_agent": "Mozilla/5.0 Test",
        "cookies": [
            {"name": "sid", "value": "1", "domain": ".a.com"},
            {"name": "other", "value": "2", "domain": "b.com"},
        ],
    }}
    headers, cookies = browser_request_extras(extra, "https://dl.a.com/f.zip")
    assert headers == {"Referer": "https://a.com/page", "User-Agent": "Mozilla/5.0 Test"}
    assert cookies == {"sid": "1"}


def test_rejects_bad_referrer_and_header_injection():
    extra = {"browser": {"referrer": "javascript:alert(1)",
                         "user_agent": "UA\r\nX-Evil: 1", "cookies": []}}
    assert browser_request_extras(extra, "https://a.com/f") == ({}, {})


def test_manager_passes_browser_extras_to_download():
    src = (ROOT / "src" / "opengrab" / "engine" / "manager.py").read_text(encoding="utf-8")
    assert "browser_request_extras(link.extra, url)" in src
    assert "**b_cookies" in src and "**b_headers" in src
