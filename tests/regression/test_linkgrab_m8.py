import pytest
from opengrab.linkgrab import extract_urls, normalize_url, dedupe


def test_extract_urls():
    assert extract_urls("see https://a.com/x.zip, and http://b.com/y.") == ["https://a.com/x.zip", "http://b.com/y"]
    assert extract_urls("hxxps://c.com/f.bin") == ["https://c.com/f.bin"]
    assert extract_urls("ftp://d.com/a.iso magnet:?xt=urn:btih:abc") == ["ftp://d.com/a.iso", "magnet:?xt=urn:btih:abc"]
    assert extract_urls("(https://e.com/p)") == ["https://e.com/p"]
    assert extract_urls("no links here") == []
    assert extract_urls("https://A.com/x https://a.com:443/x#top") == ["https://A.com/x"]


def test_normalize_url():
    assert normalize_url("HTTP://Example.COM:80") == "http://example.com/"
    assert normalize_url("https://x.com:8443/a?b=1#f") == "https://x.com:8443/a?b=1"
    assert normalize_url(" magnet:?xt=urn:btih:abc ") == "magnet:?xt=urn:btih:abc"


def test_dedupe():
    assert dedupe(["https://a.com/1", "https://A.com/1", "https://a.com/2"], existing=["https://a.com/2#x"]) == ["https://a.com/1"]



def test_extract_urls_hxxp_case_insensitive():
    assert extract_urls("HXXP://f.com/a and HxxPs://g.com/b") == ["http://f.com/a", "https://g.com/b"]
