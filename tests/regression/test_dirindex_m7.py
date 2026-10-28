import pytest
from opengrab.plugins.dirindex import is_dir_index, parse_dir_index, split_entries


def test_is_dir_index():
    """Test the is_dir_index function with various inputs."""
    # Apache-style page with Index of in title
    apache_sample = """
    <html>
    <head><title>Index of /pub/</title></head>
    <body>
    <h1>Index of /pub/</h1>
    <!-- content -->
    </body>
    </html>
    """
    assert is_dir_index(apache_sample) == True
    
    # Page without Index of
    assert is_dir_index("<html><title>Home</title></html>") == False
    
    # nginx-style page with only h1
    nginx_sample = "<h1>Index of /files/</h1>"
    assert is_dir_index(nginx_sample) == True


def test_parse_dir_index():
    """Test the parse_dir_index function with a sample Apache-style directory listing."""
    # Sample directory index HTML as described in the task
    html_content = """
    <html>
    <head><title>Index of /pub/</title></head>
    <body>
    <h1>Index of /pub/</h1>
    <table>
    <tr><td><a href="?C=N;O=D">Sort by Name</a></td></tr>
    <tr><td><a href="../">Parent Directory</a></td></tr>
    <tr><td><a href="a%20file.zip">a file.zip</a></td></tr>
    <tr><td><a href="docs/">docs/</a></td></tr>
    <tr><td><a href="b.iso">b.iso</a></td></tr>
    <tr><td><a href="b.iso">b.iso</a></td></tr>
    <tr><td><a href="https://other.invalid/x.zip">x.zip</a></td></tr>
    </table>
    </body>
    </html>
    """
    
    base_url = "https://mirror.invalid/pub/"
    
    result = parse_dir_index(html_content, base_url)
    
    # Expected result
    expected = [
        ("https://mirror.invalid/pub/a%20file.zip", "a file.zip"),
        ("https://mirror.invalid/pub/docs/", "docs/"),
        ("https://mirror.invalid/pub/b.iso", "b.iso")
    ]
    
    assert result == expected


def test_split_entries():
    """Test the split_entries function."""
    # Test with sample data from parse_dir_index
    entries = [
        ("https://mirror.invalid/pub/a%20file.zip", "a file.zip"),
        ("https://mirror.invalid/pub/docs/", "docs/"), 
        ("https://mirror.invalid/pub/b.iso", "b.iso")
    ]
    
    files, subdirs = split_entries(entries)
    
    # Should correctly separate files and directories
    assert files == [
        ("https://mirror.invalid/pub/a%20file.zip", "a file.zip"),
        ("https://mirror.invalid/pub/b.iso", "b.iso")
    ]
    
    assert subdirs == [
        ("https://mirror.invalid/pub/docs/", "docs/")
    ]


def test_single_quoted_href():
    """Test handling of single-quoted href attributes."""
    html_content = '<a href=\'c.txt\'>c.txt</a>'
    base_url = "https://mirror.invalid/pub/index.html"
    
    result = parse_dir_index(html_content, base_url)
    
    expected = [("https://mirror.invalid/pub/c.txt", "c.txt")]
    
    assert result == expected