import os
import sys

# Add the repo's src to Python path
repo_src = os.path.join(os.path.dirname(__file__), '..', 'src')
sys.path.insert(0, repo_src)

class MockLink:
    def __init__(self):
        self.id = 12345
        self.href = "http://example.com/file.zip"
        self.filename = "original.zip"
        self.status = "idle"

def test_content_disposition_parsing():
    # Test 1: Simple filename with quotes
    header = 'attachment; filename="a b.zip"'
    assert _cd_filename(header) == 'a b.zip'

    # Test 2: UTF-8 encoded filename with percent encoding
    header = "attachment; filename*=UTF-8''%E2%82%AC%20rates.txt"
    assert _cd_filename(header) == '\u20ac rates.txt'

    # Test 3: Relative path in filename (should sanitize)
    header = 'attachment; filename="../../evil.exe"'
    assert _cd_filename(header) == 'evil.exe' 
    
    # Test 4: Empty header
    header = ''
    assert _cd_filename(header) is None

def test_integration():
    # Import the actual downloader module and check how it works
    
    from opengrab.engine.downloader import Downloader
    
    # This should not throw an error even if the function is missing
    # The full integration would require a more complex test setup
    pass

if __name__ == "__main__":
    test_content_disposition_parsing()
    test_integration()
    print("All tests passed!")