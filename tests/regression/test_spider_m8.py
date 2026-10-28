import pytest
from opengrab.spider import extract_links, crawl, SpiderOptions


def test_extract_links():
    result = extract_links(
        '<a href="/a.zip#x">1</a><img src="b.png"><a href="mailto:x@y.z">m</a><a href="/a.zip">dup</a>',
        "https://s.com/dir/",
    )
    assert result == ["https://s.com/a.zip", "https://s.com/dir/b.png"]


def test_depth():
    fake_fetch = {
        "https://s.com/": ("text/html", '<a href="/p1">1</a><a href="/f0.zip">f0</a>'),
        "https://s.com/p1": ('text/html', '<a href="/p2">1</a><a href="/f1.zip">f1</a>'),
        "https://s.com/p2": ("text/html", '<a href="/f2.zip">f2</a>'),
    }

    def fetch(url):
        if url not in fake_fetch:
            raise ConnectionError("Not found")
        return fake_fetch[url]

    # Test with max_depth=1
    result = crawl(
        "https://s.com/",
        fetch,
        SpiderOptions(max_depth=1, extensions=(".zip",)),
    )
    assert result["pages"] == ["https://s.com/", "https://s.com/p1"]
    assert result["files"] == ["https://s.com/f0.zip", "https://s.com/f1.zip"]

    # Test with max_depth=2
    result = crawl(
        "https://s.com/",
        fetch,
        SpiderOptions(max_depth=2, extensions=(".zip",)),
    )
    assert "https://s.com/f2.zip" in result["files"]


def test_same_domain():
    fake_fetch = {
        "https://s.com/": (
            "text/html",
            '<a href="https://other.com/page">other</a><a href="https://cdn.other.com/x.zip">zip</a>',
        ),
    }

    def fetch(url):
        if url not in fake_fetch:
            raise ConnectionError("Not found")
        return fake_fetch[url]

    # Same domain=True (default)
    result = crawl(
        "https://s.com/",
        fetch,
        SpiderOptions(extensions=(".zip",)),
    )
    assert result["pages"] == ["https://s.com/"]
    assert result["files"] == ["https://cdn.other.com/x.zip"]

    # Same domain=False
    result = crawl(
        "https://s.com/",
        fetch,
        SpiderOptions(extensions=(".zip",), same_domain=False),
    )
    assert "https://other.com/page" in result["pages"]
    assert result["files"] == ["https://cdn.other.com/x.zip"]


def test_include_exclude():
    fake_fetch = {
        "https://s.com/": (
            "text/html",
            '<a href="/a.zip">a</a><a href="/b.zip">b</a><a href="/c.rar">c</a>',
        ),
    }

    def fetch(url):
        if url not in fake_fetch:
            raise ConnectionError("Not found")
        return fake_fetch[url]

    # Include regex
    result = crawl(
        "https://s.com/",
        fetch,
        SpiderOptions(extensions=(".ZIP", ".rar"), include=(r"\.zip$",)),
    )
    assert result["files"] == ["https://s.com/a.zip", "https://s.com/b.zip"]

    # Exclude regex
    result = crawl(
        "https://s.com/",
        fetch,
        SpiderOptions(extensions=(".zip",), exclude=(r"/b\.",)),
    )
    assert result["files"] == ["https://s.com/a.zip"]


def test_errors_and_max_pages():
    fake_fetch = {
        "https://s.com/": ("text/html", '<a href="/missing">missing</a><a href="/f.zip">file</a>'),
        "https://s.com/f.zip": ("application/zip", "content"),
    }

    def fetch(url):
        if url not in fake_fetch:
            raise ConnectionError("Not found")
        return fake_fetch[url]

    # Missing page ends up in errors
    result = crawl("https://s.com/", fetch)
    assert "https://s.com/missing" in result["errors"]

    # JSON content type is visited but not parsed for files
    fake_fetch_with_json = {
        "https://s.com/page": ("application/json", '{"key":"value"}'),
    }

    def fetch_json(url):
        return fake_fetch_with_json[url]

    result = crawl("https://s.com/page", fetch_json)
    assert result["pages"] == ["https://s.com/page"]
    assert result["files"] == []

    # max_pages=1 visits only the start page
    fake_fetch_multi = {
        "https://s.com/": ("text/html", '<a href="/p1">1</a>'),
        "https://s.com/p1": ("text/html", '<a href="/p2">2</a>'),
    }

    def fetch_multi(url):
        return fake_fetch_multi[url]

    result = crawl("https://s.com/", fetch_multi, SpiderOptions(max_pages=1))
    assert result["pages"] == ["https://s.com/"]
    assert result["files"] == []