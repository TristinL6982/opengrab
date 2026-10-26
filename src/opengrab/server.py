"""Local HTTP API + static UI server (stdlib only, no extra deps).

Endpoints (JSON unless noted):
  GET    /api/ping                     liveness (no auth)
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
import os
import queue
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from .engine.manager import DownloadManager
from .settings import Settings, app_data_dir

log = __import__("logging").getLogger("opengrab.server")

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
        # -- token ---------------------------------------------------------
        self.token = token or settings.api_token or \
            uuid.uuid4().hex + uuid.uuid4().hex[:8]
        if settings.api_token != self.token:
            settings.api_token = self.token
            try:
                settings.save()
            except Exception:
                pass
        # -- SSE fanout ----------------------------------------------------
        self._clients: list["queue.Queue"] = []
        self._clients_lock = threading.Lock()
        prev = manager.on_change

        def fanout(kind: str, payload) -> None:
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

        import queue as _queue
        self._q = _queue
        self.httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ run
    def start(self) -> int:
        """Non-blocking start; returns the bound port."""
        handler = _make_handler(self)
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
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
        if client_ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return True
        h = headers.get("Authorization", "")
        return h == f"Bearer {self.token}"


# ------------------------------------------------------------------ handler
def _make_handler(srv: ApiServer):
    mgr: DownloadManager = srv.manager

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "OpenGrab/0.1"

        # -- plumbing ----------------------------------------------------
        def log_message(self, format, *args):  # keep console quiet
            log.debug(format, *args)

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods",
                             "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, Authorization")

        def _send(self, code: int, body: bytes,
                  ctype: str = "application/json; charset=utf-8",
                  extra: Optional[dict] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
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

        # -- verbs ---------------------------------------------------------
        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            u = urlparse(self.path)
            p = u.path.rstrip("/") or "/"
            if p.startswith("/api/"):
                self._api_get(u, p, parse_qs(u.query))
            else:
                self._static(p)

        def do_POST(self):
            u = urlparse(self.path)
            p = u.path.rstrip("/")
            if not p.startswith("/api/"):
                return self._err(404, "not found")
            if not self._authorized():
                return self._err(401, "unauthorized: send Bearer token")
            try:
                self._api_post(p, self._body())
            except Exception as e:  # noqa: BLE001
                log.exception("POST %s failed", p)
                self._err(500, str(e))

        def do_DELETE(self):
            u = urlparse(self.path)
            p = u.path.rstrip("/")
            if not p.startswith("/api/") or not self._authorized():
                return self._err(403 if p.startswith("/api/") else 404,
                                 "forbidden")
            try:
                self._api_delete(p)
            except Exception as e:  # noqa: BLE001
                self._err(500, str(e))

        # -- api -----------------------------------------------------------
        def _link(self, lid: str):
            l = mgr.get(lid)
            if l is None:
                self._err(404, "link not found")
                return None
            return l

        def _api_get(self, u, p: str, q: dict):
            if p == "/api/ping":
                return self._json({"ok": True, "app": "OpenGrab",
                                   "version": "0.1.0"})
            if not self._authorized():
                return self._err(401, "unauthorized: send Bearer token")
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
            if p == "/api/settings":
                d = mgr.settings.model_dump()
                d["api_token"] = srv.token
                d["download_dir"] = str(Path(mgr.settings.download_dir))
                return self._json(d)
            if p == "/api/plugins":
                return self._json({"plugins": mgr.registry.snapshot(),
                                   "errors": mgr.registry.errors_snapshot()})
            if p == "/api/accounts":
                return self._json(mgr.accounts.snapshot())
            if p == "/api/filters":
                return self._json(mgr.filters.snapshot())
            if p == "/api/plugins/scan":
                n = mgr.registry.scan(force=True)
                return self._json({"scanned": n})
            if p == "/api/events":
                return self._sse()
            self._err(404, "unknown endpoint")

        def _api_post(self, p: str, body: dict):
            import re
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
                    return self._err(404, "unknown action")
                return self._json({"ok": True})
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
                        ).to_public())
                    except Exception as e:  # noqa: BLE001
                        out.append({"url": u_, "error": str(e)})
                return self._json(out)
            if p == "/api/clear-finished":
                return self._json({"cleared": mgr.clear_finished()})
            if p == "/api/settings":
                for k, v in body.items():
                    if hasattr(mgr.settings, k) and k != "api_token":
                        try:
                            setattr(mgr.settings, k, v)
                        except Exception:
                            pass
                try:
                    mgr.settings.save()
                except Exception:
                    pass
                return self._json({"ok": True})
            if p == "/api/plugins/scan":
                return self._json({"scanned": mgr.registry.scan(force=True)})
            if p == "/api/accounts":
                a = mgr.accounts.add(str(body.get("site", "")),
                                     str(body.get("username", "")),
                                     str(body.get("password", "")))
                return self._json(a)
            if p == "/api/filters":
                f = mgr.filters.add(str(body.get("pattern", "")),
                                    str(body.get("kind", "exclude")),
                                    bool(body.get("use_glob", False)))
                return self._json(f)
            self._err(404, "unknown endpoint")

        def _api_delete(self, p: str):
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
                self._cors()
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
            self._send(200, data, ctype,
                       extra={"Cache-Control": "max-age=60"})

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
