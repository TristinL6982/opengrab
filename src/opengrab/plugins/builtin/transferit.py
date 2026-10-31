"""transfer.it (MEGA's transfer service) links.

The transfer.it web client talks to the same Mega API (g.api.mega.co.nz/cs)
with the transfer handle in the ``x`` query parameter:

  {"a": "xi", "xh": <xh>}            transfer info (password flag, counts)
  {"a": "f", "c": 1, "r": 1}  ?x=<xh>  node listing; node keys come unwrapped
  {"a": "g", "n": <node>, "g": 1, "pt": 1, "ssl": 1}  ?x=<xh>  download URL

Files are encrypted exactly like Mega files, so finalize() is shared.

Link forms:
  https://transfer.it/t/<xh>              the whole transfer
  https://transfer.it/t/<xh>#file=<node>  one file (the child form we emit)
Password-protected transfers are not supported yet.
"""
from __future__ import annotations

import re
from typing import Optional

from ..base import GrabResult, PluginError, ResolveResult, SitePlugin
from . import _megacore as mc

LINK_RE = re.compile(
    r"^https?://(?:www\.)?transfer\.it/t/([A-Za-z0-9_-]{12,})/?"
    r"(?:#file=([A-Za-z0-9_-]{8}))?$")

_LISTINGS = mc.TTLCache(ttl=600.0)


def parse_link(url: str) -> Optional[dict]:
    m = LINK_RE.match((url or "").strip())
    if not m:
        return None
    return {"xh": m.group(1), "node": m.group(2) or ""}


def child_url(xh: str, node: str) -> str:
    return f"https://transfer.it/t/{xh}#file={node}"


class TransferItPlugin(SitePlugin):
    id = "transferit"
    name = "Transfer.it"
    site = "transfer.it"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = "transfer.it transfers (MEGA); decrypts downloads locally."

    api_factory = mc.MegaApi

    def supports(self, url) -> bool:
        return parse_link(url) is not None

    def _api(self, ctx):
        if ctx is None or getattr(ctx, "client", None) is None:
            raise PluginError("no HTTP client available")
        return self.api_factory(ctx.client)

    def _tree(self, api, xh: str) -> dict:
        tree = _LISTINGS.get(xh)
        if tree is not None:
            return tree
        try:
            info = api.call({"a": "xi", "xh": xh})
        except mc.MegaApiError as e:
            if e.code == -9:
                raise PluginError("transfer not found (deleted, expired or the "
                                  "link is wrong) (code -9)") from e
            raise
        if isinstance(info, dict) and info.get("pw"):
            raise PluginError("this transfer is password protected, which "
                              "OpenGrab does not support yet")
        try:
            nodes = api.list_nodes({"x": xh}, ca=False)
        except mc.MegaApiError as e:
            if e.code == -14:
                raise PluginError("this transfer is password protected, which "
                                  "OpenGrab does not support yet") from e
            raise
        tree = mc.decode_plain_nodes(nodes)
        if not tree:
            raise PluginError("this transfer is empty or could not be decrypted")
        _LISTINGS.put(xh, tree)
        return tree

    def _file(self, api, xh: str, node: dict, want_url: bool) -> ResolveResult:
        if len(node["key"]) != 8:
            raise PluginError("transfer file has an invalid key")
        info = api.file_info(node=node["h"], want_url=want_url,
                             query={"x": xh}, extra={"pt": 1} if want_url else None)
        attrs_name = node["name"]
        if info.get("at"):
            try:
                attrs_name = mc.decrypt_attr(
                    info["at"], mc.unpack_file_key(node["key"])[0]).get("n") or attrs_name
            except PluginError:
                pass
        size = mc._int(info.get("s")) or node["s"] or None
        url = mc.download_url(info) if want_url else ""
        return ResolveResult(
            url=url, filename=mc.safe_name(str(attrs_name), node["h"]), size=size,
            extra={"mega": {"key": mc.b64url_encode(mc.a32_to_bytes(node["key"])),
                            "size": size, "verify_mac": True}})

    def _lookup(self, url: str, ctx, want_url: bool) -> tuple[str, ResolveResult]:
        link = parse_link(url)
        if link is None:
            raise PluginError("not a transfer.it link")
        api = self._api(ctx)
        xh = link["xh"]
        tree = self._tree(api, xh)
        if link["node"]:
            node = tree.get(link["node"])
            if node is None or node["t"] != 0:
                raise PluginError("that file is not part of this transfer")
            r = self._file(api, xh, node, want_url)
            return r.filename, r
        files = mc.files_under(tree)
        if not files:
            raise PluginError("this transfer has no files")
        if len(files) == 1:
            r = self._file(api, xh, files[0], want_url)
            return r.filename, r
        roots = [tree[h] for h in mc.find_roots(tree) if tree[h]["t"] == 1]
        title = roots[0]["name"] if roots else xh
        children = [[child_url(xh, f["h"]), f["path"]] for f in files]
        total = sum(f["s"] for f in files) or None
        return title, ResolveResult(url="", size=total, children=children)

    def grab(self, url, ctx) -> GrabResult:
        title, r = self._lookup(url, ctx, want_url=False)
        return GrabResult(title=title, filename=r.filename, size=r.size,
                          site="transfer.it", plugin_id=self.id,
                          children=list(r.children))

    def resolve(self, url, ctx) -> ResolveResult:
        return self._lookup(url, ctx, want_url=True)[1]

    def finalize(self, path, link, ctx):
        return mc.finalize_download(path, link, "mega")
