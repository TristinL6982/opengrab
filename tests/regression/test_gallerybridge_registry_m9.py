"""gallery-dl bridge registration (M9h part 2)."""
from opengrab.plugins import builtin
from opengrab.plugins.builtin.gallerybridge import GalleryDlBridgePlugin
from opengrab.plugins.registry import PluginRegistry


def test_exported_from_builtin_package():
    assert builtin.GalleryDlBridgePlugin is GalleryDlBridgePlugin
    assert "GalleryDlBridgePlugin" in builtin.__all__


def test_registry_discovers_gallerydl():
    reg = PluginRegistry()
    reg.scan(force=True)
    assert "gallerydl" in reg.plugins
    plugin = reg.plugins["gallerydl"]
    assert isinstance(plugin, GalleryDlBridgePlugin)
    assert plugin.origin == "builtin"
    assert "builtin:gallerybridge" not in reg.errors


def test_registry_plugin_claims_gallery_urls_only():
    reg = PluginRegistry()
    reg.scan(force=True)
    plugin = reg.plugins["gallerydl"]
    assert plugin.supports("https://imgur.com/a/abc")
    assert plugin.supports("https://www.deviantart.com/someone/gallery")
    assert not plugin.supports("https://www.youtube.com/watch?v=abc")


def test_youtube_not_routed_to_gallerydl():
    reg = PluginRegistry()
    reg.scan(force=True)
    ids = [p.id for p in reg.for_url("https://www.youtube.com/watch?v=abc")]
    assert "gallerydl" not in ids
    assert "ytbridge" in ids