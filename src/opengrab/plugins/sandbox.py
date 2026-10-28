"""
Plugin sandbox: wraps a plugin's HTTP client and context so every request is checked against its granted permissions.
"""

import copy
from opengrab.plugins.permissions import Permissions, PermissionError_


class GuardedClient:
    def __init__(self, client, perms: Permissions):
        self._client = client
        self._perms = perms

    def request(self, method, url, *args, **kwargs):
        self._perms.check_url(str(url))
        return self._client.request(method, url, *args, **kwargs)

    def get(self, url, *args, **kwargs):
        self._perms.check_url(str(url))
        return self._client.get(url, *args, **kwargs)

    def post(self, url, *args, **kwargs):
        self._perms.check_url(str(url))
        return self._client.post(url, *args, **kwargs)

    def head(self, url, *args, **kwargs):
        self._perms.check_url(str(url))
        return self._client.head(url, *args, **kwargs)

    def stream(self, method, url, *args, **kwargs):
        self._perms.check_url(str(url))
        return self._client.stream(method, url, *args, **kwargs)

    @property
    def cookies(self):
        self._perms.check_cookies()
        return self._client.cookies

    def __getattr__(self, name):
        return getattr(self._client, name)


def sandbox_context(ctx, perms: Permissions):
    new = copy.copy(ctx)
    new.client = GuardedClient(ctx.client, perms)
    return new