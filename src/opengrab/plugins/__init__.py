"""Plugin framework: base classes, registry, and builtin plugins."""
from .base import (SitePlugin, BridgePlugin, PluginContext, GrabResult,
                   ResolveResult, PluginError, CaptchaRequired,
                   LoginRequired, PaywallRequired)
from .registry import PluginRegistry

__all__ = [
    "SitePlugin", "BridgePlugin", "PluginContext", "GrabResult",
    "ResolveResult", "PluginError", "CaptchaRequired", "LoginRequired",
    "PaywallRequired", "PluginRegistry",
]
