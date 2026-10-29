"""Built-in plugin set."""
from .direct import DirectLinkPlugin
from .gallerybridge import GalleryDlBridgePlugin
from .generic import GenericPagePlugin
from .ytbridge import YtDlpBridgePlugin

__all__ = ["DirectLinkPlugin", "GalleryDlBridgePlugin", "GenericPagePlugin", "YtDlpBridgePlugin"]
