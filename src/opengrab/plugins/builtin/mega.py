"""MEGA (mega.nz, legacy mega.co.nz) public file and folder links.

Files are stored encrypted; the key travels in the URL fragment. resolve()
asks Mega's public API for a temporary download URL, decrypts the file name
and stores the key in link.extra["mega"]; the engine downloads the
encrypted bytes and finalize() decrypts them in place (AES-128-CTR) after
checking Mega's chunk MAC.

Link forms:
  https://mega.nz/file/<h>#<key>          https://mega.nz/#!<h>!<key>
  https://mega.nz/embed/<h>#<key>         https://mega.nz/embed#!<h>!<key>
  https://mega.nz/folder/<h>#<key>        https://mega.nz/#F!<h>!<key>
  https://mega.nz/folder/<h>#<key>/folder/<n>   (a subfolder)
  https://mega.nz/folder/<h>#<key>/file/<n>     (one file of a folder;
                                                 also the child form we emit)
  https://mega.nz/#F!<h>!<key>!<n>  or  ...?<n>  (legacy file/subfolder)
Hosts: mega.nz, mega.co.nz, mega.io, mega.app (with or without www.).
"""
from __future__ import annotations

import re
from typing import Optional

from ..base import GrabResult, PluginError, ResolveResult, SitePlugin
from . import _megacore as mc

HOST = r"https?://(?:www\.)?(?:mega\.nz|mega\.co\.nz|mega\.io|mega\.app)"
H = r"([A-Za-z0-9_-]{8})"
K = r"([A-Za-z0-9_,-]{16,})"

FILE_RES = [
    re.compile(HOST + r"/(?:file|embed)/" + H + r"(?:#" + K + r")?/?$"),
    re.compile(HOST + r"/(?:embed)?#!" + H + r"(?:!" + K + r")?/?$"),
    re.compile(HOST + r"/!" + H + r"(?:!" + K + r")?/?$"),
]
FOLDER_RE = re.compile(
    HOST + r"/folder/" + H + r"(?:#" + K + r"(?:/(file|folder)/" + H + r")?)?/?$")
LEGACY_FOLDER_RE = re.compile(
    HOST + r"/#F!" + H + r"(?:!" + K + r"(?:[!?]" + H + r")?)?/?$")

_LISTINGS = mc.TTLCache(ttl=600.0)


def parse_link(url: str) -> Optional[dict]:
    """Parse a Mega link into {kind, handle, key, sub, sub_kind}.

    kind is "file" or "folder"; for folder links sub is a node handle inside
    the folder and sub_kind is "file", "folder" or "" (legacy: unknown)."""
    url = (url or "").strip()
    for rx in FILE_RES:
        m = rx.match(url)
        if m:
            return {"kind": "file", "handle": m.group(1), "key": m.group(2) or "",
                    "sub": "", "sub_kind": ""}
    m = FOLDER_RE.match(url)
    if m:
        return {"kind": "folder", "handle": m.group(1), "key": m.group(2) or "",
                "sub": m.group(4) or "", "sub_kind": m.group(3) or ""}
    m = LEGACY_FOLDER_RE.match(url)
    if m:
        return {"kind": "folder", "handle": m.group(1), "key": m.group(2) or "",
                "sub": m.group(3) or "", "sub_kind": ""}
    return None


def child_url(folder_handle: str, folder_key: str, node: str) -> str:
    return f"https://mega.nz/folder/{folder_handle}#{folder_key}/file/{node}"


def _file_key(link: dict) -> list[int]:
    if not link["key"]:
        raise PluginError("this Mega link has no decryption key (the part after #)")
    k = mc.b64_to_a32(link["key"])
    if len(k) != 8:
        raise PluginError("the decryption key in this Mega file link is incomplete")
    return k


def _folder_key(link: dict) -> list[int]:
    if not link["key"]:
        raise PluginError("this Mega folder link has no decryption key (the part after #)")
    k = mc.b64_to_a32(link["key"])
    if len(k) != 4:
        raise PluginError("the decryption key in this Mega folder link is incomplete")
    return k


class MegaPlugin(SitePlugin):
    id = "mega"
    name = "MEGA"
    site = "mega.nz"
    origin = "builtin"
    supports_captcha = False
    supports_login = False
    description = ("MEGA public file and folder links (mega.nz, mega.co.nz); "
                   "decrypts downloads locally with the key in the link.")

    # Overridable in tests.
    api_factory = mc.MegaApi

    def supports(self, url) -> bool:
        return parse_link(url) is not None

    # -- helpers -----------------------------------------------------------
    def _api(self, ctx):
        if ctx is None or getattr(ctx, "client", None) is None:
            raise PluginError("no HTTP client available")
        return self.api_factory(ctx.client)

    def _tree(self, api, link: dict) -> dict:
        fkey = _folder_key(link)
        cache_key = (link["handle"], link["key"])
        tree = _LISTINGS.get(cache_key)
        if tree is None:
            nodes = api.list_nodes({"n": link["handle"]})
            tree = mc.decode_nodes(nodes, fkey)
            if not tree:
                raise PluginError("could not decrypt this Mega folder: the key "
                                  "in the link is wrong, or the folder is empty")
            _LISTINGS.put(cache_key, tree)
        return tree

    def _folder_package(self, link: dict, tree: dict, top: str):
        files = mc.files_under(tree, top)
        if not files:
            raise PluginError("this Mega folder has no files")
        roots = [tree[top]] if top else [tree[h] for h in mc.find_roots(tree)
                                         if tree[h]["t"] == 1]
        title = roots[0]["name"] if roots else link["handle"]
        children = [[child_url(link["handle"], link["key"], f["h"]), f["path"]]
                    for f in files]
        total = sum(f["s"] for f in files)
        return title, children, total

    def _file_from_info(self, info: dict, key: list[int], handle: str,
                        want_url: bool) -> ResolveResult:
        attrs = mc.decrypt_attr(info.get("at", ""), mc.unpack_file_key(key)[0])
        filename = mc.safe_name(str(attrs.get("n") or ""), handle)
        size = mc._int(info.get("s"))
        url = mc.download_url(info) if want_url else ""
        return ResolveResult(
            url=url, filename=filename, size=size,
            extra={"mega": {"key": mc.b64url_encode(mc.a32_to_bytes(key)),
                            "size": size, "verify_mac": True}})

    def _lookup(self, url: str, ctx, want_url: bool) -> tuple[str, ResolveResult]:
        """Returns (title, result); result has children for folders."""
        link = parse_link(url)
        if link is None:
            raise PluginError("not a Mega link")
        api = self._api(ctx)
        if link["kind"] == "file":
            key = _file_key(link)
            info = api.file_info(p=link["handle"], want_url=want_url)
            r = self._file_from_info(info, key, link["handle"], want_url)
            return r.filename, r
        tree = self._tree(api, link)
        sub = link["sub"]
        if sub:
            node = tree.get(sub)
            if node is None:
                raise PluginError("that file or subfolder is not in this Mega folder")
            if node["t"] == 0:
                if len(node["key"]) != 8:
                    raise PluginError("Mega file node has an invalid key")
                info = api.file_info(node=sub, want_url=want_url,
                                     query={"n": link["handle"]})
                r = self._file_from_info(info, node["key"], sub, want_url)
                return r.filename, r
        title, children, total = self._folder_package(link, tree, sub)
        return title, ResolveResult(url="", filename="", size=total or None,
                                    children=children)

    # -- plugin API ----------------------------------------------------------
    def grab(self, url, ctx) -> GrabResult:
        # Metadata only: no download URL here, so the engine still calls
        # resolve(), which stores the decryption key for finalize().
        title, r = self._lookup(url, ctx, want_url=False)
        return GrabResult(title=title, filename=r.filename, size=r.size,
                          site="mega.nz", plugin_id=self.id,
                          children=list(r.children))

    def resolve(self, url, ctx) -> ResolveResult:
        _title, r = self._lookup(url, ctx, want_url=True)
        return r

    def finalize(self, path, link, ctx):
        return mc.finalize_download(path, link, "mega")
