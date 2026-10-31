"""MediaFire (mediafire.com) files and folders.

Flow, as a browser visitor gets it:

* File links (``/file/<quickkey>/<name>/file``, ``/download/<key>``,
  ``/view/<key>``, ``/?<key>``): the public API
  ``/api/1.5/file/get_info.php`` gives name, size and whether the file is
  public, password protected or gone. The file page then carries the
  "Download" button whose ``href`` is the direct
  ``https://downloadNNNN.mediafire.com/...`` link (some pages put it base64
  encoded in ``data-scrambled-url``). The direct link expires, so it is read
  again on every resolve.
* Folder links (``/folder/<key>``, ``app.mediafire.com/<key>``, ``/#<key>``):
  ``/api/1.5/folder/get_content.php`` lists files and subfolders in chunks of
  100. Every file becomes its own download (its normal file page link);
  subfolders are walked too.
* If MediaFire shows a reCAPTCHA page instead of the button, the captcha goes
  to the solver chain (``ctx.solve_captcha``) and the page's form is posted
  back with the answer.
"""
from __future__ import annotations

import base64
import html as _html
import re
from urllib.parse import quote, urljoin, urlsplit

from ..base import GrabResult, PluginContext, PluginError, ResolveResult, SitePlugin

API = "https://www.mediafire.com/api/1.5"
HOSTS = ("mediafire.com", "www.mediafire.com", "m.mediafire.com", "app.mediafire.com")
KEY = r"[A-Za-z0-9]{8,20}"
FILE_PATH_RE = re.compile(rf"^/(?:file|file_premium|download|view)/({KEY})(?:/|$)", re.I)
FOLDER_PATH_RE = re.compile(rf"^/folder/({KEY})(?:/|$)", re.I)
APP_PATH_RE = re.compile(rf"^/({KEY})/?$")
QUERY_KEY_RE = re.compile(rf"^({KEY})$")
MAX_ITEMS = 5000
MAX_DEPTH = 10


def parse(url: str) -> tuple[str, str] | None:
    """Return ("file" | "folder" | "unknown", key) for a MediaFire link."""
    p = urlsplit(url.strip())
    host = p.netloc.lower()
    if p.scheme not in ("http", "https") or host not in HOSTS:
        return None
    if host == "app.mediafire.com":
        m = APP_PATH_RE.match(p.path)
        return ("folder", m.group(1)) if m else None
    m = FILE_PATH_RE.match(p.path)
    if m:
        return "file", m.group(1)
    m = FOLDER_PATH_RE.match(p.path)
    if m:
        return "folder", m.group(1)
    if p.path in ("", "/"):
        # legacy forms: /?<key> (file or folder) and /#<folderkey>
        q = p.query.split("&", 1)[0]
        if QUERY_KEY_RE.match(q):
            return "unknown", q
        if QUERY_KEY_RE.match(p.fragment):
            return "folder", p.fragment
    return None


def file_page_url(quickkey: str, filename: str = "") -> str:
    if filename:
        return f"https://www.mediafire.com/file/{quickkey}/{quote(filename)}/file"
    return f"https://www.mediafire.com/file/{quickkey}"


def parse_size(text: str) -> int | None:
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?)B", text or "", re.I)
    if not m:
        return None
    mult = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
    return int(float(m.group(1)) * mult[m.group(2).upper()])


def find_download_link(page: str) -> str:
    """The direct link on a MediaFire file page, or ""."""
    m = re.search(r'data-scrambled-url="([A-Za-z0-9+/=]+)"', page)
    if m:
        try:
            link = base64.b64decode(m.group(1)).decode("utf-8", "replace")
            if link.startswith("http"):
                return link
        except ValueError:
            pass
    m = re.search(r'href="(https?://download\d*\.mediafire\.com/[^"]+)"', page, re.I)
    return _html.unescape(m.group(1)) if m else ""


def _api(ctx: PluginContext, path: str, params: str) -> dict:
    r = ctx.client.get(f"{API}/{path}?{params}&response_format=json")
    try:
        body = r.json().get("response") or {}
    except (ValueError, AttributeError):
        raise PluginError(f"MediaFire API answered HTTP {r.status_code} without JSON")
    return body


def _api_error(body: dict) -> str:
    return str(body.get("message") or "unknown error")


class MediaFirePlugin(SitePlugin):
    id = "mediafire"
    name = "MediaFire"
    site = "mediafire.com"
    origin = "builtin"
    supports_captcha = True
    supports_login = False
    description = "MediaFire file links and folders (each folder file becomes its own download)."

    def supports(self, url: str) -> bool:
        return parse(url) is not None

    # -- grab ---------------------------------------------------------------
    def grab(self, url: str, ctx: PluginContext) -> GrabResult:
        kind, key = self._kind(url, ctx)
        if kind == "folder":
            name, children = self._folder(key, ctx)
            return GrabResult(title=name, site=self.site, plugin_id=self.id, children=children)
        info = self._file_info(key, ctx)
        return GrabResult(title=info["filename"], filename=info["filename"], size=info["size"],
                          site=self.site, plugin_id=self.id)

    # -- resolve ------------------------------------------------------------
    def resolve(self, url: str, ctx: PluginContext) -> ResolveResult:
        kind, key = self._kind(url, ctx)
        if kind == "folder":
            _name, children = self._folder(key, ctx)
            if not children:
                raise PluginError("MediaFire folder is empty")
            return ResolveResult(url="", children=children)
        info = self._file_info(key, ctx)
        page_url = file_page_url(key, info["filename"])
        r = ctx.client.get(page_url)
        if "error.php" in (r.url or "") or r.status_code == 404:
            raise PluginError("MediaFire file is offline (deleted or blocked)")
        page = r.text
        link = find_download_link(page)
        if not link:
            link = self._captcha_then_link(page, r.url or page_url, ctx)
        size = info["size"]
        if size is None:
            m = re.search(r"Download \(([\d.]+\s*[KMGT]?B)\)", page)
            size = parse_size(m.group(1)) if m else None
        return ResolveResult(url=link, filename=info["filename"], size=size,
                             headers={"referer": page_url})

    # -- helpers --------------------------------------------------------------
    def _kind(self, url: str, ctx: PluginContext) -> tuple[str, str]:
        parsed = parse(url)
        if parsed is None:
            raise PluginError("not a MediaFire file or folder link")
        kind, key = parsed
        if kind == "unknown":
            body = _api(ctx, "file/get_info.php", f"quick_key={key}")
            kind = "file" if body.get("result") == "Success" else "folder"
        return kind, key

    def _file_info(self, key: str, ctx: PluginContext) -> dict:
        body = _api(ctx, "file/get_info.php", f"quick_key={key}")
        if body.get("result") != "Success":
            if str(body.get("error")) in ("110", "111", "112"):
                raise PluginError("MediaFire file is offline (unknown or deleted key)")
            raise PluginError(f"MediaFire: {_api_error(body)}")
        info = body.get("file_info") or {}
        if str(info.get("password_protected", "no")).lower() == "yes":
            raise PluginError("MediaFire file is password protected; download it in the browser")
        if str(info.get("privacy", "public")).lower() == "private":
            raise PluginError("MediaFire file is private")
        try:
            size = int(info.get("size") or 0) or None
        except (TypeError, ValueError):
            size = None
        return {"filename": str(info.get("filename") or key), "size": size}

    def _folder(self, key: str, ctx: PluginContext) -> tuple[str, list]:
        body = _api(ctx, "folder/get_info.php", f"folder_key={key}")
        if body.get("result") != "Success":
            raise PluginError(f"MediaFire folder is offline or private: {_api_error(body)}")
        name = str((body.get("folder_info") or {}).get("name") or key)
        children: list = []
        self._walk(key, "", ctx, children, depth=0)
        return name, children

    def _walk(self, key: str, prefix: str, ctx: PluginContext, out: list, depth: int) -> None:
        if depth > MAX_DEPTH:
            return
        for item in self._chunks(key, "files", ctx):
            if len(out) >= MAX_ITEMS:
                return
            qk, fname = str(item.get("quickkey") or ""), str(item.get("filename") or "")
            if qk:
                out.append([file_page_url(qk, fname), prefix + (fname or qk)])
        for sub in self._chunks(key, "folders", ctx):
            sk = str(sub.get("folderkey") or "")
            if sk:
                self._walk(sk, f"{prefix}{sub.get('name') or sk}/", ctx, out, depth + 1)

    def _chunks(self, key: str, content_type: str, ctx: PluginContext):
        chunk = 1
        while True:
            body = _api(ctx, "folder/get_content.php",
                        f"folder_key={key}&content_type={content_type}&chunk={chunk}")
            if body.get("result") != "Success":
                raise PluginError(f"MediaFire folder listing failed: {_api_error(body)}")
            content = body.get("folder_content") or {}
            yield from content.get(content_type) or []
            if str(content.get("more_chunks", "no")).lower() != "yes":
                return
            chunk += 1

    def _captcha_then_link(self, page: str, page_url: str, ctx: PluginContext) -> str:
        m = re.search(r'data-sitekey="([^"]+)"', page)
        if not m:
            if re.search(r"dangerous|blocked for violation|taken down", page, re.I):
                raise PluginError("MediaFire file is blocked (taken down or flagged)")
            raise PluginError("MediaFire page has no download button (layout changed?)")
        token = ctx.solve_captcha(kind="recaptcha", sitekey=m.group(1), site_url=page_url)
        form = re.search(r'<form[^>]*action="([^"]*)"', page, re.I)
        action = urljoin(page_url, _html.unescape(form.group(1))) if form else page_url
        r = ctx.client.post(action, data={"g-recaptcha-response": token},
                            headers={"referer": page_url})
        link = find_download_link(r.text)
        if not link:
            raise PluginError("MediaFire rejected the captcha answer")
        return link

