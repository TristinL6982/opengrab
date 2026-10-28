import pytest
from opengrab.spider import crawl, SpiderOptions

def test_robots_blocks_page():
    """Test that robots.txt blocks pages"""
    
    # Create a fake fetch function that maps urls to (content_type, body) tuples
    fetch_calls = []
    
    def fetch(url):
        fetch_calls.append(url)
        if url == "https://s.com/robots.txt":
            return ("text/plain", "User-agent: *\nDisallow: /private\n")
        elif url == "https://s.com/":
            return ("text/html", "<html><body><a href='/private/p'>private</a><a href='/open'>open</a></body></html>")
        elif url == "https://s.com/open":
            return ("text/html", "<html><body>Open page</body></html>")
        elif url == "https://s.com/private/p":
            return ("text/html", "<html><body>Private page</body></html>")
        else:
            raise ConnectionError(f"Not found: {url}")
    
    result = crawl("https://s.com/", fetch, SpiderOptions(max_depth=1))
    
    # Verify expected results
    assert result["pages"] == ["https://s.com/", "https://s.com/open"]
    assert result["blocked"] == ["https://s.com/private/p"]
    assert result["errors"] == []
    
    # Verify that the blocked URL was never passed to fetch
    assert "https://s.com/private/p" not in fetch_calls


def test_robots_disabled():
    """Test that robots.txt is ignored when respect_robots=False"""
    
    # Create a fake fetch function that maps urls to (content_type, body) tuples
    fetch_calls = []
    
    def fetch(url):
        fetch_calls.append(url)
        if url == "https://s.com/robots.txt":
            return ("text/plain", "User-agent: *\nDisallow: /private\n")
        elif url == "https://s.com/":
            return ("text/html", "<html><body><a href='/private/p'>private</a><a href='/open'>open</a></body></html>")
        elif url == "https://s.com/open":
            return ("text/html", "<html><body>Open page</body></html>")
        elif url == "https://s.com/private/p":
            return ("text/html", "<html><body>Private page</body></html>")
        else:
            raise ConnectionError(f"Not found: {url}")
    
    result = crawl("https://s.com/", fetch, SpiderOptions(max_depth=1, respect_robots=False))
    
    # Verify expected results
    assert result["pages"] == ["https://s.com/", "https://s.com/private/p", "https://s.com/open"]
    assert result["blocked"] == []
    assert result["errors"] == []
    
    # Verify that robots.txt was never passed to fetch (since we're not respecting robots)
    assert "https://s.com/robots.txt" not in fetch_calls


def test_missing_robots_txt():
    """Test behavior when robots.txt is missing"""
    
    # Create a fake fetch function that maps urls to (content_type, body) tuples
    fetch_calls = []
    
    def fetch(url):
        fetch_calls.append(url)
        if url == "https://s.com/":
            return ("text/html", "<html><body><a href='/a'>link</a></body></html>")
        elif url == "https://s.com/a":
            return ("text/html", "<html><body>Page A</body></html>")
        else:
            raise ConnectionError(f"Not found: {url}")
    
    result = crawl("https://s.com/", fetch, SpiderOptions(max_depth=1))
    
    # Verify expected results
    assert result["pages"] == ["https://s.com/", "https://s.com/a"]
    assert result["errors"] == []
    assert result["blocked"] == []
    
    # Verify that robots.txt was fetched exactly once (and failed silently)
    assert fetch_calls.count("https://s.com/robots.txt") == 1


def test_delay():
    """Test delay functionality"""
    
    # Create a fake fetch function that maps urls to (content_type, body) tuples
    fetch_calls = []
    sleep_calls = []
    
    def fetch(url):
        fetch_calls.append(url)
        if url == "https://s.com/":
            return ("text/html", "<html><body><a href='/a'>link</a></body></html>")
        elif url == "https://s.com/a":
            return ("text/html", "<html><body><a href='/b'>link</a></body></html>")
        elif url == "https://s.com/b":
            return ("text/html", "<html><body>Final page</body></html>")
        else:
            raise ConnectionError(f"Not found: {url}")
    
    def fake_sleep(seconds):
        sleep_calls.append(seconds)
    
    result = crawl("https://s.com/", fetch, SpiderOptions(max_depth=2, delay=0.5, respect_robots=False), sleep=fake_sleep)
    
    # Verify expected results
    assert result["pages"] == ["https://s.com/", "https://s.com/a", "https://s.com/b"]
    assert result["errors"] == []
    assert result["blocked"] == []
    
    # Verify that sleep was called twice (for the 2nd and 3rd page)
    assert sleep_calls == [0.5, 0.5]
    
    # Test with delay=0
    sleep_calls.clear()
    result = crawl("https://s.com/", fetch, SpiderOptions(max_depth=2, delay=0, respect_robots=False), sleep=fake_sleep)
    
    # Verify that sleep was not called at all
    assert sleep_calls == []