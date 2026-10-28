"""
Tests for LinkGrabber package grouping.
"""

from opengrab.packages import filename_from_url, package_name, group_packages


def test_filename_from_url():
    """Test filename extraction from URLs."""
    assert filename_from_url("https://a.com/dir/My%20File.zip?x=1") == "My File.zip"
    assert filename_from_url("https://a.com/") == ""
    assert filename_from_url("magnet:?xt=urn:btih:abc&dn=Ubuntu%2024.iso") == "Ubuntu 24.iso"
    assert filename_from_url("magnet:?xt=urn:btih:abc") == ""


def test_package_name():
    """Test package name derivation from filenames."""
    assert package_name("movie.part1.rar") == "movie"
    assert package_name("movie.PART02.rar") == "movie"
    assert package_name("b.7z.001") == "b"
    assert package_name("c.r00") == "c"
    assert package_name("c.z01") == "c"
    assert package_name("c.zip") == "c"
    assert package_name("x.tar.gz") == "x.tar"
    assert package_name(".bashrc") == ".bashrc"
    assert package_name("README") == "README"


def test_group_packages():
    """Test grouping of URLs by package."""
    urls = [
        "https://h.com/m.part1.rar",
        "https://h.com/",
        "https://h.com/m.part2.rar",
        "https://h.com/n.iso"
    ]
    
    result = group_packages(urls)
    expected = {
        "m": ["https://h.com/m.part1.rar", "https://h.com/m.part2.rar"],
        "Various": ["https://h.com/"],
        "n": ["https://h.com/n.iso"]
    }
    
    assert result == expected
    assert list(result.keys()) == ["m", "Various", "n"]