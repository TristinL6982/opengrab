"""FileDitch (fileditch.com) file links.

FileDitch file links live on its storage hosts, for example
``https://fileditchfiles.st/<folder>/<random-id>/<name>``,
``https://itsfileditchfiles.st/...``, ``https://fileditchvids.st/...`` (videos),
``https://fileditchhot.st/...`` (hotlinks of the paid Hotlink plan), the
older ``fileditchfiles.me`` / ``fileditchstuff.me`` hosts and
``.../file.php?f=/<path>`` links; ``embedditch.st`` is the video embed host.

* Metadata comes from FileDitch's documented, deliberately open Status API:
  ``GET https://fileditchfiles.st/api/<path>`` answers
  ``{"status": true|false|"unknown", "size": ...}``. ``false`` means the
  file is gone; ``"unknown"`` is transient.
* ``fileditchhot.st`` links are direct downloads (that is what the Hotlink
  plan sells), so they are returned as they are.
* Every other host puts a JavaScript/WebAssembly gate or a Cloudflare
  managed challenge in front of the file, on purpose, for anything that is
  not a browser. OpenGrab does not work around that gate: such links raise
  "FileDitch needs the browser download flow" after the status check.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin

STATUS_API = "https://fileditchfiles.st/api"
HOT_HOST = "fileditchhot.st"
HOST_RE = re.compile(
    r"^(?:www\.)?(?:(?:its)?fileditch(?:files|vids|hot|stuff)?\.(?:st|me|com|ch)"
    r"|embedditch\.st|[a-z0-9-]+\.fileditch\.com)$")
PAGE_HOSTS = ("fileditch.com", "www.fileditch.com", "new.fileditch.com",
              "remote.fileditch.com")


def file_path(url: str) -> str | None:
    """The ``/<folder>/<id>/<name>`` path of a FileDitch file link, else None."""
    p = urlsplit(url.strip())
    host = p.netloc.lower()
    if p.scheme not in ("http", "https") or not HOST_RE.match(host) or host in PAGE_HOSTS:
        return None
    path = p.path
    if path.endswith("/file.php"):
        path = (parse_qs(p.query).get("f") or [""])[0]
    path = "/" + unquote(path).lstrip("/")
    parts = [x for x in path.split("/") if x]
    if len(parts) < 2 or parts[0] == "api" or any(x in (".", "..") for x in parts):
        return None
    return path


def status(ctx: PluginContext, path: str) -> dict:
    r = ctx.client.get(STATUS_API + quote(path, safe="/"))
    try:
        body = r.json()
    except ValueError:
        body = None
    if r.status_code == 429:
        raise PluginError("FileDitch status API rate limit; try again in a minute")
    if not isinstance(body, dict) or "status" not in body:
        raise PluginError(f"FileDitch status API answered HTTP {r.status_code}")
    return body


class FileDitchPlugin(SitePlugin):
    id = "fileditch"
    name = "FileDitch"
    site = "fileditch.com"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "FileDitch file links (status check; hotlinks download directly)."

    def supports(self, url: str) -> bool:
        return file_path(url) is not None

    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        path = file_path(url)
        if path is None:
            raise PluginError("not a FileDitch file link")
        info = self._check(path, ctx)
        name = path.rsplit("/", 1)[-1]
        return GrabResult(title=name, filename=name, size=info.get("size"), site=self.site,
                          plugin_id=self.id)

    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        path = file_path(url)
        if path is None:
            raise PluginError("not a FileDitch file link")
        info = self._check(path, ctx)
        name = path.rsplit("/", 1)[-1]
        if urlsplit(url).netloc.lower() == HOT_HOST:
            return ResolveResult(url=f"https://{HOT_HOST}{quote(path, safe='/')}",
                                 filename=name, size=info.get("size"))
        raise PluginError("FileDitch needs the browser download flow "
                          "(its file pages are gated against non-browser clients)")

    @staticmethod
    def _check(path: str, ctx: PluginContext) -> dict:
        body = status(ctx, path)
        st = body.get("status")
        if st is False:
            raise PluginError("FileDitch file is offline (deleted, expired or never existed)")
        if st is not True:  # "unknown" is a truthy string
            raise PluginError("FileDitch storage did not answer; try again in a minute")
        size = body.get("size")
        return {"size": int(size) if isinstance(size, int) and size > 0 else None}
