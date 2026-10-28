import types
from opengrab.plugins.builtin.generic import GenericPagePlugin
from opengrab.plugins.base import PluginContext

# Test Apache-style directory index page
PAGE = """<html>
<head><title>Index of /pub/</title></head>
<body>
<h1>Index of /pub/</h1>
<table>
<tr><th>Name</th><th>Size</th></tr>
<tr><td><a href="?C=N;O=D">Name</a></td><td>-</td></tr>
<tr><td><a href="/">Parent Directory</a></td><td>-</td></tr>
<tr><td><a href="a.zip">a.zip</a></td><td>1024</td></tr>
<tr><td><a href="docs/">docs/</a></td><td>4096</td></tr>
<tr><td><a href="b.iso">b.iso</a></td><td>2048</td></tr>
<tr><td><a href="https://other.invalid/x.zip">x.zip</a></td><td>512</td></tr>
</table>
</body>
</html>"""

class FakeClient:
    def get(self, url, timeout=None):
        return types.SimpleNamespace(
            status_code=200,
            text=PAGE,
            url=url,
            headers={}
        )

def test_generic_dirindex():
    settings = types.SimpleNamespace(grab_timeout_s=5)
    ctx = PluginContext(client=FakeClient(), settings=settings)
    
    g = GenericPagePlugin().grab("https://mirror.invalid/pub/", ctx)
    
    # Should extract files from directory index
    assert g.children == [
        ("https://mirror.invalid/pub/a.zip", "a.zip"),
        ("https://mirror.invalid/pub/b.iso", "b.iso")
    ]
    
    # Should mark as directory index
    assert g.extra["dir_index"] is True
    
    # Should identify subdirectories
    assert g.extra["subdirs"] == ["https://mirror.invalid/pub/docs/"]

def test_generic_normal_page():
    settings = types.SimpleNamespace(grab_timeout_s=5)
    ctx = PluginContext(client=FakeClient(), settings=settings)
    
    # Test normal page without directory index
    normal_page = "<html><title>Home</title><a href='file.zip'>file.zip</a></html>"
    
    # Replace the text in the fake client
    class FakeClient2:
        def get(self, url, timeout=None):
            return types.SimpleNamespace(
                status_code=200,
                text=normal_page,
                url=url,
                headers={}
            )
    
    ctx.client = FakeClient2()
    
    g = GenericPagePlugin().grab("https://site.invalid/", ctx)
    
    # Should extract links normally
    assert g.children == [("https://site.invalid/file.zip", "file.zip")]
    
    # Should not mark as directory index 
    assert "dir_index" not in g.extra
