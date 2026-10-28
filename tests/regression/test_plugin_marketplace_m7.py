import pytest
from opengrab.plugins.marketplace import (
    MarketplaceError,
    parse_listing,
    parse_catalog,
    search,
    updates_available,
    verify_download
)


def test_parse_listing_full_dict():
    """Test parse_listing with a full dictionary."""
    data = {
        "id": "test-plugin",
        "name": "Test Plugin",
        "version": "1.0.0",
        "url": "https://example.com/plugin.zip",
        "sha256": "ABC",
        "author": "Test Author",
        "description": "A test plugin",
        "rating": 4.5,
        "ratings_count": 100,
        "signature": "sig123",
        "permissions": {"hosts": ["a.com"]}
    }
    
    listing = parse_listing(data)
    
    assert listing.id == "test-plugin"
    assert listing.name == "Test Plugin"
    assert listing.version == "1.0.0"
    assert listing.url == "https://example.com/plugin.zip"
    assert listing.sha256 == "abc"  # Should be lowercase
    assert listing.author == "Test Author"
    assert listing.description == "A test plugin"
    assert listing.rating == 4.5
    assert listing.ratings_count == 100
    assert listing.signature == "sig123"
    assert listing.permissions.hosts == ["a.com"]


def test_parse_listing_missing_id():
    """Test parse_listing raises MarketplaceError for missing id."""
    data = {
        "name": "Test Plugin",
        "version": "1.0.0",
        "url": "https://example.com/plugin.zip",
        "sha256": "abc"
    }
    
    with pytest.raises(MarketplaceError, match="listing missing field: id"):
        parse_listing(data)


def test_parse_listing_missing_name():
    """Test parse_listing raises MarketplaceError for missing name."""
    data = {
        "id": "test-plugin",
        "version": "1.0.0",
        "url": "https://example.com/plugin.zip",
        "sha256": "abc"
    }
    
    with pytest.raises(MarketplaceError, match="listing missing field: name"):
        parse_listing(data)


def test_parse_listing_missing_version():
    """Test parse_listing raises MarketplaceError for missing version."""
    data = {
        "id": "test-plugin",
        "name": "Test Plugin",
        "url": "https://example.com/plugin.zip",
        "sha256": "abc"
    }
    
    with pytest.raises(MarketplaceError, match="listing missing field: version"):
        parse_listing(data)


def test_parse_listing_missing_url():
    """Test parse_listing raises MarketplaceError for missing url."""
    data = {
        "id": "test-plugin",
        "name": "Test Plugin",
        "version": "1.0.0",
        "sha256": "abc"
    }
    
    with pytest.raises(MarketplaceError, match="listing missing field: url"):
        parse_listing(data)


def test_parse_listing_missing_sha256():
    """Test parse_listing raises MarketplaceError for missing sha256."""
    data = {
        "id": "test-plugin",
        "name": "Test Plugin",
        "version": "1.0.0",
        "url": "https://example.com/plugin.zip"
    }
    
    with pytest.raises(MarketplaceError, match="listing missing field: sha256"):
        parse_listing(data)


def test_parse_listing_rating_too_high():
    """Test parse_listing raises MarketplaceError for rating > 5."""
    data = {
        "id": "test-plugin",
        "name": "Test Plugin",
        "version": "1.0.0",
        "url": "https://example.com/plugin.zip",
        "sha256": "abc",
        "rating": 5.5
    }
    
    with pytest.raises(MarketplaceError, match="rating out of range"):
        parse_listing(data)


def test_parse_listing_rating_too_low():
    """Test parse_listing raises MarketplaceError for rating < 0."""
    data = {
        "id": "test-plugin",
        "name": "Test Plugin",
        "version": "1.0.0",
        "url": "https://example.com/plugin.zip",
        "sha256": "abc",
        "rating": -1
    }
    
    with pytest.raises(MarketplaceError, match="rating out of range"):
        parse_listing(data)


def test_parse_catalog_duplicate_id():
    """Test parse_catalog raises MarketplaceError for duplicate IDs."""
    data = {
        "plugins": [
            {
                "id": "x",
                "name": "Plugin 1",
                "version": "1.0.0",
                "url": "https://example.com/plugin1.zip",
                "sha256": "abc"
            },
            {
                "id": "x",
                "name": "Plugin 2",
                "version": "1.0.0",
                "url": "https://example.com/plugin2.zip",
                "sha256": "def"
            }
        ]
    }
    
    with pytest.raises(MarketplaceError, match="duplicate listing id: x"):
        parse_catalog(data)


def test_parse_catalog_empty():
    """Test parse_catalog with empty data."""
    assert parse_catalog({}) == []


def test_search_rating_sorting():
    """Test search function with rating sorting."""
    listings = [
        {"id": "b", "name": "Plugin B", "version": "1.0.0", "url": "https://example.com/b.zip", "sha256": "abc", "rating": 4.0, "ratings_count": 10},
        {"id": "c", "name": "Plugin C", "version": "1.0.0", "url": "https://example.com/c.zip", "sha256": "def", "rating": 4.0, "ratings_count": 20},
        {"id": "a", "name": "Plugin A", "version": "1.0.0", "url": "https://example.com/a.zip", "sha256": "ghi", "rating": 5.0}
    ]
    
    # Convert to Listing objects
    from opengrab.plugins.marketplace import parse_listing
    parsed_listings = [parse_listing(item) for item in listings]
    
    result = search(parsed_listings, sort="rating")
    assert [l.id for l in result] == ["a", "c", "b"]


def test_search_name_sorting():
    """Test search function with name sorting."""
    listings = [
        {"id": "b", "name": "Alpha Plugin", "version": "1.0.0", "url": "https://example.com/b.zip", "sha256": "abc"},
        {"id": "c", "name": "Beta Plugin", "version": "1.0.0", "url": "https://example.com/c.zip", "sha256": "def"},
        {"id": "a", "name": "Alpha Beta Plugin", "version": "1.0.0", "url": "https://example.com/a.zip", "sha256": "ghi"}
    ]
    
    # Convert to Listing objects
    from opengrab.plugins.marketplace import parse_listing
    parsed_listings = [parse_listing(item) for item in listings]
    
    result = search(parsed_listings, sort="name")
    assert [l.id for l in result] == ["a", "b", "c"]


def test_search_with_query():
    """Test search function with query filtering."""
    listings = [
        {"id": "a", "name": "Dropbox Plugin", "version": "1.0.0", "url": "https://example.com/a.zip", "sha256": "abc", "description": "Plugin for Dropbox"},
        {"id": "b", "name": "Google Drive Plugin", "version": "1.0.0", "url": "https://example.com/b.zip", "sha256": "def"}
    ]
    
    # Convert to Listing objects
    from opengrab.plugins.marketplace import parse_listing
    parsed_listings = [parse_listing(item) for item in listings]
    
    result = search(parsed_listings, query="DROP")
    assert len(result) == 1
    assert result[0].id == "a"


def test_search_unknown_sort():
    """Test search function raises MarketplaceError for unknown sort."""
    listings = [
        {"id": "a", "name": "Plugin A", "version": "1.0.0", "url": "https://example.com/a.zip", "sha256": "abc"}
    ]
    
    from opengrab.plugins.marketplace import parse_listing
    parsed_listings = [parse_listing(item) for item in listings]
    
    with pytest.raises(MarketplaceError, match="unknown sort: bogus"):
        search(parsed_listings, sort="bogus")


def test_updates_available():
    """Test updates_available function."""
    listings = [
        {"id": "a", "name": "Plugin A", "version": "1.1.0", "url": "https://example.com/a.zip", "sha256": "abc"},
        {"id": "b", "name": "Plugin B", "version": "2.0.0", "url": "https://example.com/b.zip", "sha256": "def"},
        {"id": "c", "name": "Plugin C", "version": "1.0.0", "url": "https://example.com/c.zip", "sha256": "ghi"}
    ]
    
    from opengrab.plugins.marketplace import parse_listing
    parsed_listings = [parse_listing(item) for item in listings]
    
    installed = {"a": "1.0.0", "b": "2.0.0"}
    
    result = updates_available(parsed_listings, installed)
    assert len(result) == 1
    assert result[0].id == "a"


def test_verify_download_correct():
    """Test verify_download with correct data and no verifier."""
    # Correct SHA256 hash for "test data"
    listing = {"id": "test", "name": "Test", "version": "1.0.0", "url": "https://example.com/test.zip", "sha256": "916f0027a575074ce72a331777c3478d6513f786a591bd892da1a577bf2335f9"}
    from opengrab.plugins.marketplace import parse_listing
    parsed_listing = parse_listing(listing)
    
    # Correct data that matches the sha256 (SHA256 of "test data")
    data = b"test data"
    
    verify_download(parsed_listing, data)  # Should not raise


def test_verify_download_wrong_sha256():
    """Test verify_download raises MarketplaceError for wrong SHA256."""
    listing = {"id": "test", "name": "Test", "version": "1.0.0", "url": "https://example.com/test.zip", "sha256": "916f0027a575074ce72a331777c3478d6513f786a591bd892da1a577bf2335f9"}
    from opengrab.plugins.marketplace import parse_listing
    parsed_listing = parse_listing(listing)
    
    # Data that doesn't match the sha256
    data = b"wrong data"
    
    with pytest.raises(MarketplaceError, match="sha256 mismatch for test"):
        verify_download(parsed_listing, data)


def test_verify_download_unsigned():
    """Test verify_download raises MarketplaceError for unsigned plugin."""
    listing = {"id": "test", "name": "Test", "version": "1.0.0", "url": "https://example.com/test.zip", "sha256": "916f0027a575074ce72a331777c3478d6513f786a591bd892da1a577bf2335f9", "signature": ""}
    from opengrab.plugins.marketplace import parse_listing
    parsed_listing = parse_listing(listing)
    
    data = b"test data"
    
    with pytest.raises(MarketplaceError, match="unsigned plugin: test"):
        verify_download(parsed_listing, data, verifier=lambda l, d: True)


def test_verify_download_bad_signature():
    """Test verify_download raises MarketplaceError for bad signature."""
    listing = {"id": "test", "name": "Test", "version": "1.0.0", "url": "https://example.com/test.zip", "sha256": "916f0027a575074ce72a331777c3478d6513f786a591bd892da1a577bf2335f9", "signature": "sig123"}
    from opengrab.plugins.marketplace import parse_listing
    parsed_listing = parse_listing(listing)
    
    data = b"test data"
    
    with pytest.raises(MarketplaceError, match="bad signature for test"):
        verify_download(parsed_listing, data, verifier=lambda l, d: False)


def test_verify_download_good_signature():
    """Test verify_download passes with good signature and verifier."""
    listing = {"id": "test", "name": "Test", "version": "1.0.0", "url": "https://example.com/test.zip", "sha256": "916f0027a575074ce72a331777c3478d6513f786a591bd892da1a577bf2335f9", "signature": "sig123"}
    from opengrab.plugins.marketplace import parse_listing
    parsed_listing = parse_listing(listing)
    
    data = b"test data"
    
    # Record if verifier was called
    calls = []
    
    def mock_verifier(l, d):
        calls.append((l, d))
        return True
    
    verify_download(parsed_listing, data, verifier=mock_verifier)
    
    assert len(calls) == 1
    assert calls[0][0] is parsed_listing
    assert calls[0][1] == data