"""Local HTTP API + static UI server (stdlib only, no extra deps).

Endpoints (JSON unless noted):
  GET    /api/ping                     liveness (no auth)
  POST   /api/linkgrab/analyze         {text:str} -> {count, packages}
  POST   /api/linkgrab/container       {filename:str, data:base64} -> {count, packages}
  GET    /api/links                    all links (public dicts)
  POST   /api/urls                     {urls:[...], priority?:int, auto_download?:bool}
  GET    /api/stats                    counts + aggregate speed
  POST   /api/links/{id}/start|pause|resume|stop|remove
  POST   /api/links/{id}/retry         (alias of start for error state)
  POST   /api/links/{id}/queue-child   {index:int}
  POST   /api/links/{id}/queue-children
  POST   /api/links/{id}/captcha       {code:str}
  POST   /api/links/{id}/login         {username:str, password:str}
  POST   /api/links/{id}/paywall-accept
  DELETE /api/links/{id}
  POST   /api/clear-finished
  GET    /api/settings   /api/plugins  /api/accounts  /api/filters
  POST   /api/settings   /api/plugins/scan  /api/accounts  /api/filters
  DELETE /api/accounts/{site}  /api/filters/{index}
  GET    /api/events                     SSE live event stream
  GET    /                               static UI (index.html)
  GET    /<static file>

Auth:  requests from localhost are always allowed.  Any other client must
send ``Authorization: Bearer <token>`` — the token is auto-generated on
first start and stored in settings.json (``api_token``).  Browsers on the
LAN open ``http://host:port/#token=<token>`` and the UI stores it.

Run standalone:  python -m opengrab.server  [--port N] [--host H] [--token T]
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import hmac
import re  # Added for host checks
import ipaddress  # Added for IP address checks  
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, quote, urlparse
import secrets

from .engine.manager import DownloadManager
from .settings import Settings, app_data_dir
from .access import AccessGate
from .remote import is_loopback, policy_from_dict
from .routes import Request, Router

log = __import__("logging").getLogger("opengrab.server")
# Returned by a route handler that already wrote its own response.
_HANDLED = object()

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".woff2": "font/woff2",
}


def static_dir() -> Path:
    """UI assets — bundled under ui/ in source and in the frozen exe."""
    frozen_base = getattr(sys := __import__("sys"), "_MEIPASS", None)
    base = Path(frozen_base) if frozen_base else Path(__file__).parent
    return base / "ui" / "static"


def find_free_port(host: str, start: int = 5678, tries: int = 40) -> int:
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free port found")


class ApiServer:
    """Threading HTTP server bound to a DownloadManager instance."""

    def __init__(self, settings: Settings, manager: DownloadManager,
                 host: str = "127.0.0.1", port: int = 5678,
                 token: Optional[str] = None):
        self.settings = settings
        self.manager = manager
        self.host = host
        self.port = port
        raw_base_path = str(settings.base_path or "").strip()
        if raw_base_path in ("", "/"):
            self.base_path = ""
        else:
            if (not raw_base_path.startswith("/") or "\\" in raw_base_path
                    or any(ord(char) < 32 for char in raw_base_path)
                    or "?" in raw_base_path or "#" in raw_base_path):
                raise ValueError("base_path must be a URL path beginning with '/'")
            segments = [part for part in raw_base_path.split("/") if part]
            if any(part in (".", "..") for part in segments):
                raise ValueError("base_path cannot contain dot segments")
            self.base_path = "/" + "/".join(segments)
        self.settings.base_path = self.base_path
        # -- token ---------------------------------------------------------
        self.token = token or settings.api_token or \
            uuid.uuid4().hex + uuid.uuid4().hex[:8]
        if settings.api_token != self.token:
            settings.api_token = self.token
            try:
                settings.save()
            except Exception:
                pass
        # -- M12 access gate: pairing, remote policy, auth rate limit -------
        self.access = AccessGate.load(
            app_data_dir(), self.token,
            policy=policy_from_dict({
                "allow_remote_http": bool(settings.allow_remote_http),
                "trusted_proxies": list(settings.trusted_proxies or []),
            }))
        # -- M12f browser extension interception rules --------------------
        self.extrules_path = os.path.join(str(app_data_dir()), "extension_rules.json")
        # -- nonce ------------------------------------------------------------
        self.nonce = secrets.token_hex(16)
        # -- SSE fanout ----------------------------------------------------
        self._clients: list["queue.Queue"] = []
        self._clients_lock = threading.Lock()
        # SSE tickets: EventSource cannot send an Authorization header, so
        # the UI trades its token for a short-lived, single-use ticket.
        self._sse_tickets: dict[str, float] = {}
        self._sse_tickets_lock = threading.Lock()
        self._terminal_jobs: dict[str, dict] = {}
        self._terminal_lock = threading.Lock()
        self._audio_tickets: dict[str, dict] = {}
        self._audio_tickets_lock = threading.Lock()
        self._clipboard_candidates: dict[str, dict] = {}
        self._clipboard_candidates_lock = threading.Lock()
        self.clipboard_status = "off"
        prev = manager.on_change

        def fanout(kind: str, payload) -> None:
            # W1: notifications are recorded by features/notify.py
            msg = json.dumps({"kind": kind, "payload": payload},
                             default=str)
            with self._clients_lock:
                dead = []
                for q in self._clients:
                    try:
                        q.put_nowait(msg)
                    except Exception:
                        dead.append(q)
                for q in dead:
                    try:
                        self._clients.remove(q)
                    except ValueError:
                        pass
            prev(kind, payload)

        manager.on_change = fanout
        self.httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        # -- features wired in after M12 (see routes.py, features/) --------
        self.router = Router()
        # (method, regex, fn) routes that carry their own credential (e.g. a
        # one-time key in the URL) and are dispatched before the bearer check
        self.public_routes: list = []
        # callables run with the list of applied keys after POST /api/settings
        self.settings_hooks: list = []
        from .features import install_all
        self.features = install_all(self)

    def broadcast(self, kind: str, payload) -> None:
        """Send one event to every live UI/SSE client (not a link event)."""
        msg = json.dumps({"kind": kind, "payload": payload}, default=str)
        with self._clients_lock:
            for q in list(self._clients):
                try:
                    q.put_nowait(msg)
                except Exception:
                    pass

    def capture_clipboard_text(self, text: str) -> int:
        """Queue filtered URL candidates for user review; never auto-add them."""
        import re
        if (not self.settings.clipboard_monitor_enabled
                or not isinstance(text, str) or not text):
            return 0
        found = re.findall(r"(?:https?://|magnet:\?)[^\s<>\"']+", text,
                           flags=re.IGNORECASE)
        existing = {link.url for link in self.manager.all_links()}
        added = 0
        with self._clipboard_candidates_lock:
            for raw_url in found:
                url = raw_url.rstrip(".,;:!?)]}")
                if not url or url in existing or any(
                        item["url"] == url
                        for item in self._clipboard_candidates.values()):
                    continue
                allowed, _ = self.manager.filters.allows(url, "")
                if not allowed:
                    continue
                candidate_id = uuid.uuid4().hex
                self._clipboard_candidates[candidate_id] = {
                    "id": candidate_id, "url": url, "seen_at": time.time()
                }
                added += 1
            while len(self._clipboard_candidates) > 200:
                self._clipboard_candidates.pop(next(iter(self._clipboard_candidates)))
        return added

    def add_candidate_link(self, candidate: dict):
        """Turn a reviewed LinkGrabber entry into a download (W8 keeps the
        package name and archive passwords from Click'n'Load)."""
        link = self.manager.add_url(candidate["url"])
        if candidate.get("package"):
            link.extra["package"] = candidate["package"]
        if candidate.get("passwords"):
            link.extra["archive_passwords"] = list(candidate["passwords"])
        if candidate.get("source"):
            link.extra["source"] = candidate["source"]
        self.manager._persist()
        return link

    # ------------------------------------------------------------------ run
    def start(self) -> int:
        """Non-blocking start; returns the bound port."""
        import socket
        handler = _make_handler(self)
        class _ExclusiveHTTPServer(ThreadingHTTPServer):
            # Exclusive bind: never SO_REUSEADDR; on Windows set
            # SO_EXCLUSIVEADDRUSE *before* bind so a second server on the
            # same port fails with OSError instead of silently sharing it.
            allow_reuse_address = False

            def server_bind(self_inner):
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    self_inner.socket.setsockopt(
                        socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                super().server_bind()

        self.httpd = _ExclusiveHTTPServer((self.host, self.port), handler)
        self.httpd.daemon_threads = True

        # port 0 -> OS-picked ephemeral; record the real bound port
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        name="api-server", daemon=True)
        self._thread.start()
        log.info("API server on http://%s:%d", self.host, self.port)
        return self.port

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        log.info("API server stopped")

    def wait(self) -> None:
        if self._thread:
            self._thread.join()

    # ---------------------------------------------------------------- auth
    def authorize(self, client_ip: str, headers) -> bool:
        h = headers.get("Authorization", "")
        if hmac.compare_digest(h, f"Bearer {self.token}"):
            return True
        access = getattr(self, "access", None)
        return access is not None and access.scope_for(headers) is not None

    @staticmethod
    def remote_transport_allowed(client_ip: str, headers,
                                 is_tls: bool = False) -> bool:
        """Allow localhost HTTP or HTTPS connections, including TLS proxies.

        This HTTP server does not terminate TLS itself. A loopback reverse
        proxy must explicitly mark its encrypted public scheme and client IP.
        Direct non-local HTTP, including requests with spoofed forwarding
        headers, is rejected.
        """
        if is_tls:
            return True
        local = client_ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
        if not local:
            return False
        forwarded = (headers.get("X-Forwarded-For") or headers.get("Forwarded")
                     or headers.get("X-Forwarded-Proto"))
        if not forwarded:
            return True
        proto = (headers.get("X-Forwarded-Proto") or "").lower()
        if not proto:
            forwarded_value = headers.get("Forwarded", "")
            proto = next((part.split("=", 1)[1].strip(' "').lower()
                          for part in forwarded_value.split(";")
                          if part.strip().lower().startswith("proto=")), "")
        return proto == "https"


# ------------------------------------------------------------------ handler
def _make_handler(srv: ApiServer):
    mgr: DownloadManager = srv.manager

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "OpenGrab/0.1"

        def _origin_ok(self) -> bool:
            """Reject cross-origin writes: Origin (if sent) must match Host."""
            from urllib.parse import urlparse
            origin = self.headers.get("Origin")
            if not origin:
                return True  # non-browser client or same-origin without Origin
            if origin.strip().lower() == "null":
                return False
            host = (self.headers.get("Host") or "").strip().lower()
            if not host:
                return False
            try:
                netloc = urlparse(origin.strip()).netloc.lower()
            except ValueError:
                return False
            return netloc == host

        def _is_host_allowed(self, host_header: str | None) -> bool:
            # If Host header is missing, assume it's from loopback (safe)
            if not host_header:
                return True
                
            # Parse the host and optionally get port
            # Examples: 'localhost', '127.0.0.1', '[::1]:8080', 'localhost:8080'
            host = host_header.strip().lower()
            if host.startswith("["):
                # IPv6 literal, optionally with a port: [::1] or [::1]:8080
                host = host[1:host.find("]")] if "]" in host else host
            elif host.count(":") == 1:
                host = host.rsplit(":", 1)[0]

            # Simplified checks for standard localhost/loopback types
            if host in {"127.0.0.1", "localhost", "::1"}:
                return True
            
            # Allow any IP address in loopback subnet or localhost domain  
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_loopback or (ip.version == 6 and str(ip) == "::1"):
                    return True
                elif host.endswith(".localhost"):
                    return True
            except ValueError:
                # Not an IP address, so check against allowed hosts
                pass

            # Allow specific bind host for same machine access, or when in LAN mode
            if srv.host and srv.host not in ("0.0.0.0", "::"):
                try:
                    bind_ip = ipaddress.ip_address(srv.host)
                    if bind_ip.is_loopback or str(bind_ip) == "::1":
                        allowed_hosts = {srv.host, "127.0.0.1", "localhost", "::1"}
                    else: 
                        allowed_hosts = {srv.host}
                except ValueError:
                    # srv.host is hostname not IP
                    allowed_hosts = {srv.host}
            else:
                # When binding to all interfaces, any IP is allowed
                # The authorization logic still prevents access from outside the machine  
                allowed_hosts = set()

            if host in allowed_hosts:
                return True
                
            # If the request carries a valid Authorization header with the API token, we allow it
            auth_header = self.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ", 1)[1]
                if token == srv.token:
                    return True

            # In LAN mode (binding to all interfaces)
            if srv.host in ("0.0.0.0", "::"):
                # Allow any valid IP address as valid for local access when host is zero IP
                try:
                    ip_addr = ipaddress.ip_address(host)  
                    return True  # All IP addresses allowed in LAN mode
                except ValueError:
                    # Non-IP name, reject (not safe)
                    pass

            # Any other host is not allowed.
            return False

        # -- plumbing ----------------------------------------------------
        def log_message(self, format, *args):  # keep console quiet
            log.debug(format, *args)



        def _send(self, code: int, body: bytes,
                  ctype: str = "application/json; charset=utf-8",
                  extra: Optional[dict] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, default=str).encode("utf-8"))

        def _err(self, code: int, msg: str) -> None:
            self._json({"error": msg}, code)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return {}

        def _authorized(self) -> bool:
            ip = self.client_address[0]
            return srv.authorize(ip, self.headers)

        def _transport_allowed(self) -> bool:
            is_tls = isinstance(self.connection, ssl.SSLSocket)
            if srv.remote_transport_allowed(self.client_address[0], self.headers, is_tls):
                return True
            return srv.access.transport_allowed(self.client_address[0], self.headers, is_tls)

        def _gate(self, action: str):
            """Return the caller's scope, or None after sending the error."""
            code, result = srv.access.check(self.client_address[0], self.headers, action)
            if code != 200:
                self._err(code, result)
                return None
            return result

        def _route_path(self, path: str) -> str:
            prefix = srv.base_path
            if prefix and (path == prefix or path.startswith(prefix + "/")):
                path = path[len(prefix):] or "/"
            return path.rstrip("/") or "/"

        def _download_root(self) -> Path:
            root = Path(mgr.settings.download_dir).expanduser().resolve()
            root.mkdir(parents=True, exist_ok=True)
            return root

        def _safe_file(self, relative: str, *, must_exist: bool = False) -> Path:
            if not isinstance(relative, str) or "\x00" in relative:
                raise ValueError("invalid path")
            root = self._download_root()
            rel = Path(relative)
            if rel.is_absolute():
                raise ValueError("absolute paths are not allowed")
            result = (root / rel).resolve()
            try:
                result.relative_to(root)
            except ValueError as exc:
                raise ValueError("path is outside the download directory") from exc
            if must_exist and not result.exists():
                raise FileNotFoundError("file or directory not found")
            return result

        def _serve_file(self, file_path: Path, *, attachment: bool) -> None:
            size = file_path.stat().st_size
            start, end, code = 0, size - 1, 200
            range_header = self.headers.get("Range", "")
            if range_header:
                import re
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                if not match or (not match.group(1) and not match.group(2)):
                    return self._send(416, b"", extra={"Content-Range": f"bytes */{size}"})
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else size - 1
                else:
                    suffix = int(match.group(2))
                    start = max(0, size - suffix)
                if start >= size or end < start:
                    return self._send(416, b"", extra={"Content-Range": f"bytes */{size}"})
                end = min(end, size - 1)
                code = 206
            length = max(0, end - start + 1)
            ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            headers = {
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
            }
            if code == 206:
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            if attachment:
                safe_name = "".join(
                    "_" if ord(char) < 32 or ord(char) == 127 or char in '"\\' else char
                    for char in file_path.name
                )
                headers["Content-Disposition"] = (
                    f"attachment; filename=\"{safe_name}\"; "
                    f"filename*=UTF-8''{quote(file_path.name)}"
                )
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            
            if code == 206:
                self.send_header("Content-Range", headers["Content-Range"])
            if attachment:
                self.send_header("Content-Disposition", headers["Content-Disposition"])
            self.end_headers()
            with file_path.open("rb") as stream:
                stream.seek(start)
                remaining = length
                while remaining:
                    chunk = stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def _file_listing(self, relative: str):
            path = self._safe_file(relative, must_exist=True)
            if not path.is_dir():
                return self._err(400, "path is not a directory")
            root = self._download_root()
            items = []
            for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
                try:
                    resolved = self._safe_file(str(child.relative_to(root)))
                    info = resolved.stat()
                except (OSError, ValueError, RuntimeError):
                    continue
                items.append({
                    "name": child.name,
                    "path": resolved.relative_to(root).as_posix(),
                    "type": "directory" if resolved.is_dir() else "file",
                    "size": 0 if resolved.is_dir() else info.st_size,
                    "modified": info.st_mtime,
                })
            return self._json({
                "path": path.relative_to(root).as_posix() if path != root else "",
                "items": items,
            })

        # -- verbs ---------------------------------------------------------
        def do_OPTIONS(self):
            # Check host header for security (DNS rebinding prevention)
            if not self._is_host_allowed(self.headers.get("Host")):
                return self._err(403, "forbidden host")
            self.send_response(204)
            
            self.end_headers()

        def do_GET(self):
            # Check host header for security (DNS rebinding prevention)
            if not self._is_host_allowed(self.headers.get("Host")):
                return self._err(403, "forbidden host")
            if not self._transport_allowed():
                return self._err(403, "remote access requires HTTPS")
            u = urlparse(self.path)
            if srv.base_path and u.path == srv.base_path:
                self.send_response(308)
                self.send_header("Location", srv.base_path + "/")
                self.end_headers()
                return
            p = self._route_path(u.path)
            if p.startswith("/api/"):
                try:
                    self._api_get(u, p, parse_qs(u.query))
                except FileNotFoundError as e:
                    self._err(404, str(e))
                except (ValueError, OSError, subprocess.SubprocessError) as e:
                    self._err(400, str(e))
                except Exception as e:  # noqa: BLE001
                    log.exception("GET %s failed", p)
                    self._err(500, str(e))
            else:
                self._static(p)

        def do_POST(self):
            if not self._origin_ok():
                return self._err(403, "forbidden origin")
            # Check host header for security (DNS rebinding prevention)
            if not self._is_host_allowed(self.headers.get("Host")):
                return self._err(403, "forbidden host")
            if not self._transport_allowed():
                return self._err(403, "remote access requires HTTPS")
            u = urlparse(self.path)
            p = self._route_path(u.path)
            if not p.startswith("/api/"):
                return self._err(404, "not found")
            if p == "/api/pair/claim":
                code, payload = srv.access.pair_claim(
                    self.client_address[0], self.headers, self._body())
                return self._json(payload, code)
            if p == "/api/pair/start":
                if not is_loopback(srv.access.client_ip(self.client_address[0], self.headers)):
                    return self._err(403, "pairing can only be started on this computer")
                scope = self._gate("pair_start")
                if scope is None:
                    return
                code, payload = srv.access.pair_start(scope)
                return self._json(payload, code)
            if self._public_route("POST", p, parse_qs(u.query), None):
                return
            if not self._authorized():
                return self._err(401, "unauthorized: send Bearer token")
            try:
                self._api_post(p, self._body())
            except Exception as e:  # noqa: BLE001
                log.exception("POST %s failed", p)
                self._err(500, str(e))

        def do_DELETE(self):
            if not self._origin_ok():
                return self._err(403, "forbidden origin")
            # Check host header for security (DNS rebinding prevention)
            if not self._is_host_allowed(self.headers.get("Host")):
                return self._err(403, "forbidden host")
            if not self._transport_allowed():
                return self._err(403, "remote access requires HTTPS")
            u = urlparse(self.path)
            p = self._route_path(u.path)
            if p.startswith("/api/devices/"):
                if self._gate("devices_revoke") is None:
                    return
                code, payload = srv.access.devices_revoke(p[len("/api/devices/"):])
                return self._json(payload, code)
            if not p.startswith("/api/") or not self._authorized():
                return self._err(403 if p.startswith("/api/") else 404,
                                 "forbidden")
            try:
                self._api_delete(p, parse_qs(u.query))
            except Exception as e:  # noqa: BLE001
                self._err(500, str(e))

        def do_PUT(self):
            if not self._origin_ok():
                return self._err(403, "forbidden origin")
            # Check host header for security (DNS rebinding prevention)
            if not self._is_host_allowed(self.headers.get("Host")):
                return self._err(403, "forbidden host")
            if not self._transport_allowed():
                return self._err(403, "remote access requires HTTPS")
            u = urlparse(self.path)
            if self._route_path(u.path) != "/api/files/upload":
                return self._err(404, "not found")
            if not self._authorized():
                return self._err(401, "unauthorized: send Bearer token")
            try:
                self._upload_file(parse_qs(u.query))
            except (ValueError, FileNotFoundError, FileExistsError, OSError) as e:
                self._err(400, str(e))

        def _clipboard(self, value: Optional[str] = None):
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                user32.OpenClipboard.argtypes = [wintypes.HWND]
                user32.OpenClipboard.restype = wintypes.BOOL
                user32.CloseClipboard.restype = wintypes.BOOL
                user32.GetClipboardData.argtypes = [wintypes.UINT]
                user32.GetClipboardData.restype = wintypes.HANDLE
                kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
                kernel32.GlobalLock.restype = ctypes.c_void_p
                kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
                kernel32.GlobalUnlock.restype = wintypes.BOOL
                if not user32.OpenClipboard(None):
                    raise OSError("Windows clipboard is busy")
                try:
                    if value is None:
                        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
                        if not handle:
                            return ""
                        pointer = kernel32.GlobalLock(handle)
                        if not pointer:
                            raise OSError("could not read Windows clipboard")
                        try:
                            return ctypes.wstring_at(pointer)
                        finally:
                            kernel32.GlobalUnlock(handle)
                    user32.EmptyClipboard.restype = wintypes.BOOL
                    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
                    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
                    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
                    kernel32.GlobalFree.restype = wintypes.HGLOBAL
                    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
                    user32.SetClipboardData.restype = wintypes.HANDLE
                    raw = value.encode("utf-16-le") + b"\0\0"
                    handle = kernel32.GlobalAlloc(0x0002, len(raw))  # GMEM_MOVEABLE
                    if not handle:
                        raise OSError("could not allocate clipboard memory")
                    pointer = kernel32.GlobalLock(handle)
                    if not pointer:
                        kernel32.GlobalFree(handle)
                        raise OSError("could not lock clipboard memory")
                    ctypes.memmove(pointer, raw, len(raw))
                    kernel32.GlobalUnlock(handle)
                    if not user32.EmptyClipboard() or not user32.SetClipboardData(13, handle):
                        kernel32.GlobalFree(handle)
                        raise OSError("could not write Windows clipboard")
                    return None
                finally:
                    user32.CloseClipboard()

            if sys.platform == "darwin":
                command = ["pbpaste"] if value is None else ["pbcopy"]
            elif os.environ.get("WAYLAND_DISPLAY"):
                command = ["wl-paste", "-n"] if value is None else ["wl-copy"]
            elif shutil.which("xclip"):
                command = ["xclip", "-selection", "clipboard", "-out"] if value is None else ["xclip", "-selection", "clipboard", "-in"]
            elif shutil.which("xsel"):
                command = ["xsel", "--clipboard", "--output"] if value is None else ["xsel", "--clipboard", "--input"]
            else:
                raise OSError("no supported clipboard utility is available")
            proc = subprocess.run(
                command, input=None if value is None else value.encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
                check=True,
            )
            return proc.stdout.decode("utf-8", "replace") if value is None else None

        def _upload_file(self, query: dict) -> None:
            relative = (query.get("path") or [""])[0]
            target = self._safe_file(relative)
            if target == self._download_root() or not target.parent.is_dir():
                raise ValueError("upload path must name a file in an existing directory")
            try:
                length = int(self.headers.get("Content-Length") or "-1")
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0:
                raise ValueError("Content-Length is required")
            overwrite = (query.get("overwrite") or ["false"])[0].lower() == "true"
            if target.exists() and not overwrite:
                raise FileExistsError("destination already exists")
            fd, temporary = tempfile.mkstemp(prefix=".opengrab-upload-",
                                             dir=str(target.parent))
            try:
                with os.fdopen(fd, "wb") as output:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(64 * 1024, remaining))
                        if not chunk:
                            raise OSError("upload ended before Content-Length bytes arrived")
                        output.write(chunk)
                        remaining -= len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if overwrite:
                    os.replace(temporary, target)
                else:
                    try:
                        os.link(temporary, target)
                    except (NotImplementedError, OSError) as link_error:
                        if isinstance(link_error, FileExistsError):
                            raise
                        target_fd = os.open(
                            target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                        )
                        try:
                            with os.fdopen(target_fd, "wb") as destination, open(temporary, "rb") as source:
                                shutil.copyfileobj(source, destination, 64 * 1024)
                        except Exception:
                            try:
                                target.unlink()
                            except OSError:
                                pass
                            raise
                self._json({"ok": True, "path": target.relative_to(self._download_root()).as_posix()}, 201)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

        def _files_post(self, body: dict) -> None:
            action = str(body.get("action", ""))
            relative = str(body.get("path", ""))
            target = self._safe_file(relative, must_exist=action in {"delete", "rename"})
            root = self._download_root()
            if action == "mkdir":
                if target == root:
                    raise ValueError("choose a directory name")
                target.mkdir(parents=True, exist_ok=False)
            elif action == "delete":
                if target == root:
                    raise ValueError("the download directory itself cannot be deleted")
                if target.is_dir():
                    target.rmdir()  # non-empty directories are deliberately not recursive
                else:
                    target.unlink()
            elif action == "rename":
                if target == root:
                    raise ValueError("the download directory itself cannot be renamed")
                destination = self._safe_file(str(body.get("to", "")))
                if destination == root or destination.exists():
                    raise FileExistsError("destination already exists or is invalid")
                if not destination.parent.is_dir():
                    raise FileNotFoundError("destination directory not found")
                target.rename(destination)
            else:
                return self._err(400, "action must be mkdir, delete, or rename")
            return self._json({"ok": True})

        def _terminal_authorized(self) -> bool:
            if not mgr.settings.terminal_enabled:
                self._err(403, "terminal access is disabled")
                return False
            if self.headers.get("Authorization", "") != f"Bearer {srv.token}":
                self._err(401, "terminal access requires the API bearer token")
                return False
            return True

        def _terminal_start(self, body: dict):
            if not self._terminal_authorized():
                return
            command = body.get("command")
            if not isinstance(command, str) or not command.strip() or len(command) > 4096 or "\x00" in command:
                return self._err(400, "command must be a non-empty string of at most 4096 characters")
            cwd = self._safe_file(str(body.get("cwd", "")), must_exist=True)
            if not cwd.is_dir():
                return self._err(400, "working directory must be a directory inside downloads")
            try:
                timeout_s = min(300.0, max(1.0, float(body.get("timeout_s", 30))))
            except (TypeError, ValueError):
                return self._err(400, "timeout_s must be a number")
            with srv._terminal_lock:
                for key, job in list(srv._terminal_jobs.items()):
                    if (not job["running"] and time.time() - job["started"] > 3600):
                        del srv._terminal_jobs[key]
                if len(srv._terminal_jobs) >= 100:
                    return self._err(429, "too many retained terminal sessions")
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            try:
                process = subprocess.Popen(
                    command, cwd=str(cwd), shell=True, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
            except OSError as exc:
                return self._err(400, f"could not start command: {exc}")
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "process": process, "started": time.time(),
                   "timeout_s": timeout_s, "running": True, "returncode": None,
                   "output": "", "truncated": False, "timed_out": False}
            with srv._terminal_lock:
                srv._terminal_jobs[job_id] = job

            def collect_output():
                limit = 256 * 1024
                try:
                    while True:
                        chunk = process.stdout.read(8192)
                        if not chunk:
                            break
                        decoded = chunk.decode("utf-8", "replace")
                        with srv._terminal_lock:
                            combined = job["output"] + decoded
                            if len(combined) > limit:
                                job["output"] = combined[-limit:]
                                job["truncated"] = True
                            else:
                                job["output"] = combined
                finally:
                    process.stdout.close()

            def monitor():
                reader = threading.Thread(target=collect_output, daemon=True)
                reader.start()
                try:
                    process.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    job["timed_out"] = True
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                reader.join(timeout=5)
                with srv._terminal_lock:
                    job["returncode"] = process.poll()
                    job["running"] = False

            threading.Thread(target=monitor, daemon=True,
                             name=f"terminal-{job_id[:8]}").start()
            return self._json({"id": job_id, "running": True}, 202)

        def _terminal_poll(self, job_id: str):
            if not self._terminal_authorized():
                return
            with srv._terminal_lock:
                job = srv._terminal_jobs.get(job_id)
                if job is None:
                    return self._err(404, "terminal session not found")
                return self._json({
                    "id": job_id, "running": job["running"],
                    "returncode": job["returncode"], "output": job["output"],
                    "truncated": job["truncated"], "timed_out": job["timed_out"],
                })

        def _terminal_stop(self, job_id: str):
            if not self._terminal_authorized():
                return
            with srv._terminal_lock:
                job = srv._terminal_jobs.get(job_id)
                if job is None:
                    return self._err(404, "terminal session not found")
                process = job["process"]
                if job["running"]:
                    try:
                        process.terminate()
                    except OSError:
                        pass
            return self._json({"ok": True})

        # -- api -----------------------------------------------------------
        def _link(self, lid: str):
            l = mgr.get(lid)
            if l is None:
                self._err(404, "link not found")
                return None
            return l

        def _public_route(self, method: str, p: str, q: dict, body) -> bool:
            import re as _re
            for m_, pattern, fn in srv.public_routes:
                if m_ != method:
                    continue
                match = _re.fullmatch(pattern, p)
                if not match:
                    continue
                if method == "GET":
                    fn(self, *match.groups(), q)
                else:
                    fn(self, *match.groups(), q, self._body() if body is None else body)
                return True
            return False

        def _route(self, method: str, p: str, q: dict, body) -> bool:
            route, params = srv.router.match(method, p)
            if route is None:
                return False
            scope = None
            if route.action:
                scope = self._gate(route.action)
                if scope is None:
                    return True
            req = Request(method=method, path=p, params=params, query=q or {},
                          body=body if isinstance(body, dict) else {}, srv=srv,
                          handler=self, scope=scope)
            try:
                code, payload = srv.router.call(route, req)
            except FileNotFoundError as e:
                code, payload = 404, {"error": str(e)}
            except (ValueError, KeyError) as e:
                code, payload = 400, {"error": str(e)}
            if payload is _HANDLED:
                return True
            self._json(payload, code)
            return True

        def _api_get(self, u, p: str, q: dict):
            if p in ("/api/ping", "/api/health"):
                return self._json({"ok": True, "app": "OpenGrab",
                                   "version": "0.1.0", "nonce": srv.nonce})
            if p == "/api/terminal":
                if not self._terminal_authorized():
                    return
                with srv._terminal_lock:
                    jobs = [{"id": job["id"], "running": job["running"],
                             "returncode": job["returncode"]}
                            for job in srv._terminal_jobs.values()]
                return self._json({"jobs": jobs})
            import re as _re
            terminal_match = _re.fullmatch(r"/api/terminal/([a-f0-9]+)/?", p)
            if terminal_match:
                return self._terminal_poll(terminal_match.group(1))
            if p == "/api/audio" and q.get("ticket"):
                ticket = q["ticket"][0]
                with srv._audio_tickets_lock:
                    record = srv._audio_tickets.get(ticket)
                    for key, value in list(srv._audio_tickets.items()):
                        if value["expires"] < time.time():
                            del srv._audio_tickets[key]
                if record and record["expires"] >= time.time():
                    file_path = self._safe_file(record["path"], must_exist=True)
                    if not file_path.is_file():
                        return self._err(404, "audio file not found")
                    return self._serve_file(file_path, attachment=False)
            if self._public_route("GET", p, q, None):
                return
            if p == "/api/events" and q.get("ticket"):
                with srv._sse_tickets_lock:
                    expires = srv._sse_tickets.pop(q["ticket"][0], 0)
                if expires >= time.time():
                    return self._sse()
                return self._err(401, "event ticket expired or unknown")
            if not self._authorized():
                return self._err(401, "unauthorized: send Bearer token")
            if p == "/api/extension/config":
                from .extrules import load_rules, public_config
                return self._json(public_config(load_rules(srv.extrules_path)))
            if p == "/api/devices":
                if self._gate("devices_list") is None:
                    return
                code, payload = srv.access.devices_list()
                return self._json(payload, code)
            if p == "/api/links":
                return self._json([l.to_public() for l in mgr.all_links()])
            import re as _re
            m = _re.fullmatch(r"/api/links/([^/]+)/captcha-image", p)
            if m:
                l = mgr.get(m.group(1))
                if l is None or not l.captcha_image:
                    return self._err(404, "no captcha image")
                return self._json({"image": l.captcha_image})
            if p == "/api/stats":
                return self._json(mgr.stats())
            if p == "/api/files":
                return self._file_listing((q.get("path") or [""])[0])
            if p == "/api/files/download" or p == "/api/audio":
                file_path = self._safe_file(
                    (q.get("path") or [""])[0], must_exist=True
                )
                if not file_path.is_file():
                    return self._err(404, "file not found")
                return self._serve_file(file_path,
                                        attachment=p == "/api/files/download")
            if p == "/api/clipboard":
                return self._json({"text": self._clipboard()})
            if p == "/api/clipboard/status":
                return self._json({"status": srv.clipboard_status})
            if p == "/api/linkgrabber":
                with srv._clipboard_candidates_lock:
                    return self._json(list(srv._clipboard_candidates.values()))
            if p == "/api/captcha-test":
                return self._json({
                    "selected": mgr.settings.captcha_provider,
                    "providers": mgr.captcha_chain.status(),
                })
            if p == "/api/settings":
                d = mgr.settings.model_dump()
                d["download_dir"] = str(Path(mgr.settings.download_dir))
                d.pop("api_token", None)  # Never include api_token in settings response
                d.pop("site_credentials", None)  # protected secrets stay server-side
                vt = dict(d.get("virustotal") or {})  # the VirusTotal key never leaves the server
                vt["api_key_set"] = bool(vt.pop("api_key", ""))
                d["virustotal"] = vt
                return self._json(d)
            if p == "/api/diagnostics":
                links = mgr.all_links()
                counts: dict[str, int] = {}
                for link in links:
                    key = link.status.value
                    counts[key] = counts.get(key, 0) + 1
                plugins = mgr.registry.snapshot()
                errors = mgr.registry.errors_snapshot()
                from . import __version__
                return self._json({
                    "generated_at": time.time(),
                    "app_version": __version__,
                    "python_version": sys.version.split()[0],
                    "platform": sys.platform,
                    "queue_counts": counts,
                    "plugin_ids": sorted(str(p.get("id", "")) for p in plugins),
                    "plugin_error_count": len(errors),
                    "settings": {
                        "api_host": mgr.settings.api_host,
                        "api_port": mgr.settings.api_port,
                        "base_path_configured": bool(mgr.settings.base_path),
                        "max_parallel": mgr.settings.max_parallel,
                        "per_link_chunks": mgr.settings.per_link_chunks,
                        "terminal_enabled": mgr.settings.terminal_enabled,
                        "clipboard_monitor_enabled": mgr.settings.clipboard_monitor_enabled,
                    },
                })
            if p == "/api/plugins":
                snap = mgr.registry.snapshot()
                return self._json({"plugins": snap,
                                   "errors": mgr.registry.errors_snapshot(),
                                   "health": {d["id"]: mgr.registry.health_for(d["id"]) for d in snap}})
            if p == "/api/accounts":
                return self._json(mgr.accounts.snapshot())
            if p == "/api/filters":
                return self._json(mgr.filters.snapshot())
            if p == "/api/packagizer":
                return self._json(mgr.settings.packagizer_rules)
            if p == "/api/plugins/scan":
                n = mgr.registry.scan(force=True)
                return self._json({"scanned": n})
            if p == "/api/events":
                return self._sse()
            if self._route("GET", p, q, {}):
                return
            self._err(404, "unknown endpoint")

        def _api_post(self, p: str, body: dict):
            import re
            if p == "/api/terminal":
                return self._terminal_start(body)
            if p == "/api/packagizer/preview":
                from .engine.packagizer import plan_packagizer
                try:
                    return self._json(plan_packagizer(
                        mgr.settings.packagizer_rules,
                        str(body.get("url", "")),
                        str(body.get("filename", "")),
                        mgr.settings.download_dir,
                    ))
                except Exception as exc:
                    return self._err(400, str(exc))
            if p == "/api/packagizer":
                from .engine.packagizer import PackagizerRule
                if len(mgr.settings.packagizer_rules) >= 100:
                    return self._err(400, "at most 100 Packagizer rules are supported")
                try:
                    rule = PackagizerRule.model_validate(body)
                except Exception as exc:
                    return self._err(400, str(exc))
                mgr.settings.packagizer_rules.append(rule.model_dump())
                mgr.settings.save()
                return self._json(rule.model_dump(), 201)
            terminal_stop = re.fullmatch(r"/api/terminal/([a-f0-9]+)/stop", p)
            if terminal_stop:
                return self._terminal_stop(terminal_stop.group(1))
            m = re.fullmatch(r"/api/links/([^/]+)(/(\w+))?", p)
            if m:
                lid, action = m.group(1), (m.group(3) or "")
                l = self._link(lid)
                if l is None:
                    return
                if action in ("start", "retry"):
                    mgr.start_download(lid)
                elif action == "pause":
                    mgr.pause_download(lid)
                elif action == "resume":
                    mgr.resume_download(lid)
                elif action == "stop":
                    mgr.stop_download(lid)
                elif action == "remove":
                    mgr.remove_link(lid)
                elif action == "queue-child":
                    c = mgr.queue_child(lid, int(body.get("index", 0)))
                    return self._json(c.to_public() if c else {"error": "no such child"})
                elif action == "queue-children":
                    cs = mgr.queue_all_children(lid)
                    return self._json([c.to_public() for c in cs])
                elif action == "captcha":
                    mgr.submit_captcha(lid, str(body.get("code", "")))
                elif action == "login":
                    mgr.add_login(lid, str(body.get("username", "")),
                                  str(body.get("password", "")))
                elif action == "paywall-accept":
                    mgr.accept_paywall(lid)
                else:
                    if self._route("POST", p, {}, body):
                        return
                    return self._err(404, "unknown action")
                return self._json({"ok": True})
            if p == "/api/extension/config":
                from .extrules import public_config, save_rules
                rules = body.get("rules") if isinstance(body.get("rules"), dict) else body
                return self._json(public_config(save_rules(srv.extrules_path, rules)))
            if p == "/api/extension/add":
                from .extrules import host_of, scope_cookies
                url_ = str(body.get("url") or "").strip()
                if not url_.lower().startswith(("http://", "https://")) or not host_of(url_):
                    return self._err(400, "url must be an http(s) URL")
                cookies = scope_cookies(body.get("cookies") or [], url_)
                link = mgr.add_url(
                    url_,
                    auto_download=bool(body.get("auto_download", True)),
                    title=str(body.get("title") or ""),
                    filename=str(body.get("filename") or ""),
                )
                if not isinstance(link.extra, dict):
                    link.extra = {}
                link.extra["browser"] = {
                    "referrer": str(body.get("referrer") or ""),
                    "user_agent": str(body.get("user_agent") or ""),
                    "page_url": str(body.get("page_url") or ""),
                    "cookies": cookies,
                }
                return self._json({"ok": True, "id": link.id,
                                   "cookies": len(cookies),
                                   "link": link.to_public()})
            if p == "/api/urls":
                urls = body.get("urls") or []
                if isinstance(urls, str):
                    urls = [x.strip() for x in urls.splitlines() if x.strip()]
                out = []
                for u_ in urls[:200]:
                    try:
                        out.append(mgr.add_url(
                            u_,
                            priority=int(body.get("priority", 0)),
                            auto_download=bool(body.get("auto_download", True)),
                            title=str(body.get("title", "")),
                            filename=str(body.get("filename", "")),
                            queue=str(body.get("queue") or ""),
                            expected_hash=str(body.get("hash") or "") if len(urls) == 1 else "",
                        ).to_public())
                    except Exception as e:  # noqa: BLE001
                        out.append({"url": u_, "error": str(e)})
                return self._json(out)
            if p == "/api/linkgrab/analyze":
                from . import grabber
                existing = [l.url for l in mgr.all_links()]
                return self._json(grabber.analyze_text(str(body.get("text") or ""), existing))
            if p == "/api/linkgrab/container":
                import base64, binascii
                from . import grabber
                from .containers import ContainerError
                existing = [l.url for l in mgr.all_links()]
                try:
                    data = base64.b64decode(str(body.get("data") or ""), validate=True)
                except (binascii.Error, ValueError):
                    return self._err(400, "data must be base64")
                try:
                    return self._json(grabber.analyze_container(str(body.get("filename") or ""), data, existing))
                except ContainerError as e:
                    return self._err(400, str(e))
            if p == "/api/clear-finished":
                return self._json({"cleared": mgr.clear_finished()})
            if p == "/api/events-ticket":
                ticket = secrets.token_hex(24)
                now = time.time()
                with srv._sse_tickets_lock:
                    for key, exp in list(srv._sse_tickets.items()):
                        if exp < now:
                            del srv._sse_tickets[key]
                    srv._sse_tickets[ticket] = now + 60
                return self._json({"ticket": ticket, "expires_in": 60})
            if p == "/api/files":
                try:
                    return self._files_post(body)
                except FileNotFoundError as e:
                    return self._err(404, str(e))
                except (ValueError, FileExistsError, OSError) as e:
                    return self._err(400, str(e))
            if p == "/api/clipboard":
                text = body.get("text", "")
                if not isinstance(text, str):
                    return self._err(400, "text must be a string")
                if len(text) > 1_000_000:
                    return self._err(413, "clipboard text exceeds 1 MB")
                self._clipboard(text)
                return self._json({"ok": True})
            if p == "/api/audio-ticket":
                relative = str(body.get("path", ""))
                file_path = self._safe_file(relative, must_exist=True)
                if not file_path.is_file():
                    return self._err(400, "audio path must name a file")
                ticket = uuid.uuid4().hex + uuid.uuid4().hex
                with srv._audio_tickets_lock:
                    now = time.time()
                    for key, value in list(srv._audio_tickets.items()):
                        if value["expires"] < now:
                            del srv._audio_tickets[key]
                    srv._audio_tickets[ticket] = {
                        "path": file_path.relative_to(self._download_root()).as_posix(),
                        "expires": now + 3600,
                    }
                return self._json({"url": f"{srv.base_path}/api/audio?ticket={ticket}",
                                   "expires_in": 3600})
            if p == "/api/linkgrabber/clear":
                with srv._clipboard_candidates_lock:
                    cleared = len(srv._clipboard_candidates)
                    srv._clipboard_candidates.clear()
                return self._json({"cleared": cleared})
            candidate_action = re.fullmatch(
                r"/api/linkgrabber/([a-f0-9]+)/(add|dismiss)", p
            )
            if candidate_action:
                candidate_id, action = candidate_action.groups()
                with srv._clipboard_candidates_lock:
                    candidate = srv._clipboard_candidates.pop(candidate_id, None)
                if candidate is None:
                    return self._err(404, "clipboard candidate not found")
                if action == "dismiss":
                    return self._json({"ok": True, "dismissed": True})
                link = srv.add_candidate_link(candidate)
                return self._json({"ok": True, "link": link.to_public()}, 201)
            if p == "/api/settings":
                if self._gate("settings_write") is None:
                    return
                if not isinstance(body, dict):
                    return self._json({"error": "settings body must be a JSON object"}, 400)
                forbidden = {"api_token", "api_host", "api_port", "base_path", "terminal_enabled",
                             "site_credentials"}  # logins go through /api/site-logins
                # settings that make OpenGrab run programs are only accepted from
                # this computer, never from LAN/remote clients (C4 lesson)
                runs_programs = {"event_hooks", "reconnect", "avscan", "archive_7zip_path",
                                 "media_ffmpeg_path", "captcha_ffmpeg_path", "captcha_tesseract_path",
                                 "allow_remote_http", "trusted_proxies",
                                 "virustotal"}  # can send downloaded files to a third party
                if not is_loopback(srv.access.client_ip(self.client_address[0], self.headers)):
                    forbidden = forbidden | runs_programs
                known = set(type(mgr.settings).model_fields)
                allowed = {k: v for k, v in body.items() if k in known and k not in forbidden}
                if isinstance(allowed.get("virustotal"), dict):
                    vt_new = dict(allowed["virustotal"])
                    if not str(vt_new.get("api_key") or "").strip():  # blank keeps the saved key
                        vt_new["api_key"] = (mgr.settings.virustotal or {}).get("api_key", "")
                    vt_new.pop("api_key_set", None)
                    allowed["virustotal"] = vt_new
                ignored = sorted(k for k in body if k not in allowed)
                try:
                    candidate = type(mgr.settings).model_validate(
                        {**mgr.settings.model_dump(), **allowed})
                except Exception as e:
                    return self._json({"error": "invalid settings", "detail": str(e)[:500]}, 400)
                for k in allowed:
                    setattr(mgr.settings, k, getattr(candidate, k))
                try:
                    mgr.settings.save()
                except Exception:
                    pass
                warnings: list = []
                for hook in srv.settings_hooks:
                    try:
                        warnings.extend(hook(sorted(allowed)) or [])
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(str(exc))
                return self._json({"ok": True, "applied": sorted(allowed), "ignored": ignored,
                                   "warnings": warnings})
            if p == "/api/plugins/scan":
                return self._json({"scanned": mgr.registry.scan(force=True)})
            if p == "/api/accounts":
                from .features.accounts_api import account_data
                try:
                    data = account_data(body)
                except ValueError as exc:
                    return self._err(400, str(exc))
                a = mgr.accounts.add(str(body.get("site", "")),
                                     str(body.get("username", "")),
                                     str(body.get("password", "")),
                                     premium=bool(body.get("premium", False)),
                                     data=data)
                # never echo the password or tokens back
                return self._json(next(x for x in mgr.accounts.snapshot() if x["id"] == a.id))
            if p == "/api/filters":
                f = mgr.filters.add(str(body.get("pattern", "")),
                                    str(body.get("kind", "exclude")),
                                    bool(body.get("use_glob", False)))
                return self._json(f)
            if self._route("POST", p, {}, body):
                return
            self._err(404, "unknown endpoint")

        def _api_delete(self, p: str, q: Optional[dict] = None):
            import re
            m = re.fullmatch(r"/api/links/([^/]+)", p)
            if m:
                mgr.remove_link(m.group(1))
                return self._json({"ok": True})
            m = re.fullmatch(r"/api/accounts/([^/]+)", p)
            if m:
                mgr.accounts.remove(m.group(1))
                return self._json({"ok": True})
            m = re.fullmatch(r"/api/filters/([^/]+)", p)
            if m:
                mgr.filters.remove(m.group(1))
                return self._json({"ok": True})
            m = re.fullmatch(r"/api/packagizer/([^/]+)", p)
            if m:
                before = len(mgr.settings.packagizer_rules)
                mgr.settings.packagizer_rules = [
                    item for item in mgr.settings.packagizer_rules
                    if str(item.get("id", "")) != m.group(1)
                ]
                if before == len(mgr.settings.packagizer_rules):
                    return self._err(404, "Packagizer rule not found")
                mgr.settings.save()
                return self._json({"ok": True})
            if self._route("DELETE", p, q or {}, {}):
                return
            self._err(404, "unknown endpoint")

        # -- SSE -----------------------------------------------------------
        def _sse(self):
            import queue as _queue
            q = _queue.Queue(maxsize=256)
            with srv._clients_lock:
                srv._clients.append(q)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                
                self.end_headers()
                self.wfile.write(b"event: hello\ndata: {}\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15)
                        self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                    except _queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with srv._clients_lock:
                    try:
                        srv._clients.remove(q)
                    except ValueError:
                        pass

        # -- static ----------------------------------------------------------
        def _static(self, p: str):
            root = static_dir().resolve()
            if p in ("/", ""):
                p = "/index.html"
            safe = (root / p.lstrip("/")).resolve()
            try:
                safe.relative_to(root)
            except ValueError:
                return self._err(403, "forbidden path")
            if not safe.is_file():
                # SPA fallback
                safe = root / "index.html"
            if not safe.is_file():
                return self._err(404, "no UI assets found (ui/static missing)")
            ctype = MIME.get(safe.suffix.lower(), "application/octet-stream")
            data = safe.read_bytes()
            if safe.name == "index.html" and srv.base_path:
                page = data.decode("utf-8")
                bootstrap = ("<script>window.OPENGRAB_BASE_PATH="
                             + json.dumps(srv.base_path) + ";</script>")
                data = page.replace("</head>", bootstrap + "</head>", 1).encode("utf-8")
            elif safe.name == "manifest.webmanifest" and srv.base_path:
                manifest = json.loads(data)
                manifest["start_url"] = srv.base_path + "/"
                data = json.dumps(manifest).encode("utf-8")
            cache = "max-age=60"
            if safe.name == "sw.js":
                from . import __version__
                data = data.replace(b"__OPENGRAB_VERSION__",
                                    __version__.encode("utf-8"))
                cache = "no-cache"
            self._send(200, data, ctype,
                       extra={"Cache-Control": cache})

    return Handler


# ------------------------------------------------------------------ main
def run(settings: Settings, host: str = "127.0.0.1", port: int = 5678,
        block: bool = False) -> tuple[ApiServer, DownloadManager, int]:
    """Convenience: build manager + server, start both.  Returns (srv, mgr, port)."""
    from .settings import user_plugins_dir
    mgr = DownloadManager(settings)
    mgr.start()
    srv = ApiServer(settings, mgr, host=host, port=port)
    bound = srv.start()
    settings.api_host = host
    settings.api_port = bound
    if not block:
        return srv, mgr, bound
    try:
        srv.wait()
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
        mgr.shutdown()
    return srv, mgr, bound


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="opengrab-server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5678)
    ap.add_argument("--token", default="")
    ap.add_argument("--auto-port", action="store_true",
                    help="pick a free port starting at --port")
    args = ap.parse_args(argv)

    logging_cfg = __import__("logging")
    logging_cfg.basicConfig(level=logging_cfg.INFO,
                            format="%(asctime)s %(name)s %(levelname)s %(message)s")

    from .settings import load_settings
    settings = load_settings()
    port = args.port
    if args.auto_port:
        port = find_free_port(args.host, port)
    srv = ApiServer(settings, DownloadManager(settings),
                    host=args.host, port=port,
                    token=args.token or None)
    try:
        mgr = srv.manager
        mgr.start()
        bound = srv.start()
    except OSError as e:
        print(f"could not bind {args.host}:{port}: {e}")
        return 1
    print(f"OpenGrab API  ->  http://{srv.host}:{bound}")
    if srv.host == "127.0.0.1":
        print("LAN access:  http://<your-ip>:%d/#token=%s" % (bound, srv.token))
    else:
        print("auth token:  %s" % srv.token)
    try:
        srv.wait()
    except KeyboardInterrupt:
        pass
    finally:
        srv.manager.shutdown()
        srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
