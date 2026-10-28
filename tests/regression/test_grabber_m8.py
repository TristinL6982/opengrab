import pytest
from opengrab.grabber import analyze_text, analyze_container, analyze_urls
from opengrab.containers import ContainerError, encode_rsdf


def test_analyze_text():
    text = """https://a.com/file.part1.rar
https://a.com/file.part2.rar
see http://b.com/x.zip"""
    result = analyze_text(text)
    
    # Count should be 3 (two rars + one zip)
    assert result["count"] == 3
    
    # Check packages list exactly matches what group_packages produces from those urls
    urls = ["https://a.com/file.part1.rar", "https://a.com/file.part2.rar", "http://b.com/x.zip"]
    expected_packages = {}
    for url in urls:
        from opengrab.packages import filename_from_url, package_name, group_packages
        filename = filename_from_url(url)
        pkg_name = package_name(filename)
        if not pkg_name:
            pkg_name = "Various"
        
        if pkg_name not in expected_packages:
            expected_packages[pkg_name] = []
        expected_packages[pkg_name].append(url)

    # Compare packages in sorted order
    assert len(result["packages"]) == 2  # two distinct packages: file, x
    package_names = [pkg["name"] for pkg in result["packages"]]
    package_names.sort()
    assert package_names == ["file", "x"]

    # Check that links are in the same order as dedupe/extract (preserving original order from extract)
    # But we need to ensure the links in each package match.
    expected_links = expected_packages["file"]
    assert result["packages"][0]["links"] == expected_links
    expected_links = expected_packages["x"]
    assert result["packages"][1]["links"] == expected_links


def test_duplicate_url():
    """Duplicate URL in text should appear once. A URL also passed in existing should be dropped."""
    text = "https://a.com/file.part1.rar\nhttps://a.com/file.part1.rar"
    
    # With no existing URLs
    result1 = analyze_text(text)
    assert result1["count"] == 1
    assert len(result1["packages"]) == 1
    assert result1["packages"][0]["name"] == "file"
    assert len(result1["packages"][0]["links"]) == 1

    # With existing URL that's the same
    existing = ["https://a.com/file.part1.rar"]
    result2 = analyze_text(text, existing)
    assert result2["count"] == 0  # Duplicate is removed
    assert len(result2["packages"]) == 0


def test_analyze_text_empty():
    """Empty or None text should return empty result."""
    result1 = analyze_text("")
    result2 = analyze_text(None)
    
    expected = {"count": 0, "packages": []}
    assert result1 == expected
    assert result2 == expected


def test_analyze_container_rsdf():
    """Test RSDF container decoding and analysis."""
    links = ["https://c.com/1.zip", "https://c.com/2.zip"]
    data = encode_rsdf(links)
    
    result = analyze_container("x.rsdf", data)
    assert result["count"] == 2
    # Links should be exactly those two
    link_set = set()
    for pkg in result["packages"]:
        link_set.update(pkg["links"])
    
    expected_links = set(links)
    assert link_set == expected_links


def test_analyze_container_errors():
    """Test error cases on unsupported/invalid containers."""
    with pytest.raises(ContainerError):
        analyze_container("x.dlc", b"")
        
    with pytest.raises(ContainerError):
        analyze_container("x.txt", b"")