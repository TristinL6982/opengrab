import pytest
from opengrab.linkcheck import check_link, check_links, summarize


def test_200_with_content_length_and_disposition():
    """200 with content-length and Content-Disposition gives state 'online', size 1234, filename 'a b.zip'."""
    
    def head(url):
        if url == "http://example.com/file.zip":
            return (200, {
                "content-length": "1234",
                "Content-Disposition": "attachment; filename=\"a b.zip\""
            })
        raise ConnectionError(f"Unexpected URL: {url}")
    
    result = check_link("http://example.com/file.zip", head)
    
    assert result.url == "http://example.com/file.zip"
    assert result.state == "online"
    assert result.size == 1234
    assert result.filename == "a b.zip"
    assert result.status_code == 200


def test_content_disposition_utf8():
    """Content-Disposition 'attachment; filename*=UTF-8''%E2%82%AC.txt' gives the euro-sign filename."""
    
    def head(url):
        if url == "http://example.com/file.zip":
            return (200, {
                "content-disposition": "attachment; filename*=UTF-8''%E2%82%AC.txt"
            })
        raise ConnectionError(f"Unexpected URL: {url}")
        
    result = check_link("http://example.com/file.zip", head)
    
    assert result.filename == "\u20ac.txt"


def test_content_disposition_plain():
    """Content-Disposition 'attachment; filename=plain.bin; x=1' gives 'plain.bin'."""
    
    def head(url):
        if url == "http://example.com/file.zip":
            return (200, {
                "content-disposition": 'attachment; filename=plain.bin; x=1'
            })
        raise ConnectionError(f"Unexpected URL: {url}")
        
    result = check_link("http://example.com/file.zip", head)
    
    assert result.filename == "plain.bin"


def test_404_offline():
    """404 gives 'offline'."""
    
    def head(url):
        if url == "http://example.com/file.zip":
            return (404, {})
        raise ConnectionError(f"Unexpected URL: {url}")
        
    result = check_link("http://example.com/file.zip", head)
    
    assert result.state == "offline"


def test_410_offline():
    """410 gives 'offline'."""
    
    def head(url):
        if url == "http://example.com/file.zip":
            return (410, {})
        raise ConnectionError(f"Unexpected URL: {url}")
        
    result = check_link("http://example.com/file.zip", head)
    
    assert result.state == "offline"


def test_500_unknown():
    """500 gives 'unknown'."""
    
    def head(url):
        if url == "http://example.com/file.zip":
            return (500, {})
        raise ConnectionError(f"Unexpected URL: {url}")
        
    result = check_link("http://example.com/file.zip", head)
    
    assert result.state == "unknown"


def test_302_online():
    """302 gives 'online'."""
    
    def head(url):
        if url == "http://example.com/file.zip":
            return (302, {})
        raise ConnectionError(f"Unexpected URL: {url}")
        
    result = check_link("http://example.com/file.zip", head)
    
    assert result.state == "online"


def test_exception_unknown():
    """A raising head gives state 'unknown' with status_code None."""
    
    def head(url):
        if url == "http://example.com/file.zip":
            raise ConnectionError("Connection failed")
        raise ConnectionError(f"Unexpected URL: {url}")
        
    result = check_link("http://example.com/file.zip", head)
    
    assert result.state == "unknown"
    assert result.status_code is None


def test_content_length_not_digits():
    """Content-Length 'abc' gives size None."""
    
    def head(url):
        if url == "http://example.com/file.zip":
            return (200, {
                "content-length": "abc"
            })
        raise ConnectionError(f"Unexpected URL: {url}")
        
    result = check_link("http://example.com/file.zip", head)
    
    assert result.size is None


def test_check_links_preserves_order():
    """check_links keeps input order."""
    
    def head(url):
        if url == "http://example.com/1":
            return (200, {})
        elif url == "http://example.com/2":
            return (404, {})
        elif url == "http://example.com/3":
            raise ConnectionError("Connection failed")
        raise ConnectionError(f"Unexpected URL: {url}")
        
    result = check_links([
        "http://example.com/1",
        "http://example.com/2", 
        "http://example.com/3"
    ], head)
    
    assert len(result) == 3
    assert result[0].state == "online"
    assert result[1].state == "offline"
    assert result[2].state == "unknown"


def test_summarize_counts():
    """summarize of 3 urls (one 200, one 404, one raising) == {'online': 1, 'offline': 1, 'unknown': 1}."""
    
    def head(url):
        if url == "http://example.com/1":
            return (200, {})
        elif url == "http://example.com/2":
            return (404, {})
        elif url == "http://example.com/3":
            raise ConnectionError("Connection failed")
        raise ConnectionError(f"Unexpected URL: {url}")
        
    result = check_links([
        "http://example.com/1",
        "http://example.com/2", 
        "http://example.com/3"
    ], head)
    
    summary = summarize(result)
    
    assert summary == {"online": 1, "offline": 1, "unknown": 1}


def test_summarize_empty():
    """summarize([]) == {'online': 0, 'offline': 0, 'unknown': 0}."""
    
    result = summarize([])
    
    assert result == {"online": 0, "offline": 0, "unknown": 0}

def test_content_disposition_quoted_with_apostrophe():
    """A quoted filename containing an apostrophe is kept whole."""

    def head(url):
        return (200, {"Content-Disposition": 'attachment; filename="it\'s here.zip"; size=5'})

    result = check_link("http://example.com/x", head)

    assert result.filename == "it's here.zip"


def test_content_disposition_missing_filename():
    """A Content-Disposition without a filename gives an empty filename."""

    def head(url):
        return (200, {"Content-Disposition": "inline"})

    result = check_link("http://example.com/x", head)

    assert result.filename == ""
