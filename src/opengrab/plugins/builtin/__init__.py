"""Built-in plugin set."""
from .direct import DirectLinkPlugin
from .generic import GenericPagePlugin
from .ytbridge import YtDlpBridgePlugin

__all__ = ["DirectLinkPlugin", "GenericPagePlugin", "YtDlpBridgePlugin"]
