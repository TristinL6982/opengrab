"""M13: the official ``opengrab`` command-line client (spec 6c).

Talks to a running OpenGrab over its token-protected API, for everyday shell
use and for AI agents::

    opengrab add URL [URL ...] [--queue NAME] [--review] [--no-start] [--hash sha256:…]
    opengrab list [--status STATUS] [--json]
    opengrab status [--json]
    opengrab pause|resume|start|stop|remove ID [ID ...] | all
    opengrab notifications [--json]

The address and token come from --url/--token, then OPENGRAB_URL /
OPENGRAB_TOKEN, then this computer's settings.json. Exit codes: 0 success,
1 the API refused, 2 bad arguments, 3 OpenGrab is not running.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

COMMANDS = ("add", "list", "status", "pause", "resume", "start", "stop", "remove", "notifications")


class ClientError(Exception):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="opengrab", description="Control a running OpenGrab from the shell.")
    p.add_argument("--url", default=None, help="OpenGrab address (default: this computer)")
    p.add_argument("--token", default=None, help="API token (default: from settings.json)")
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("add", help="add links")
    a.add_argument("urls", nargs="+")
    a.add_argument("--queue", default="")
    a.add_argument("--hash", default="", help="expected checksum for a single link")
    a.add_argument("--review", action="store_true", help="put the links in the LinkGrabber instead")
    a.add_argument("--no-start", action="store_true")
    a.add_argument("--json", action="store_true", dest="json_sub")
    ls = sub.add_parser("list", help="list downloads")
    ls.add_argument("--status", default="")
    ls.add_argument("--json", action="store_true", dest="json_sub")
    st = sub.add_parser("status", help="overall status")
    st.add_argument("--json", action="store_true", dest="json_sub")
    nt = sub.add_parser("notifications", help="recent notifications")
    nt.add_argument("--json", action="store_true", dest="json_sub")
    for name in ("pause", "resume", "start", "stop", "remove"):
        c = sub.add_parser(name, help=f"{name} downloads by id, or 'all'")
        c.add_argument("ids", nargs="+")
        c.add_argument("--json", action="store_true", dest="json_sub")
    return p


def _endpoint(args) -> tuple[str, str]:
    url = args.url or os.environ.get("OPENGRAB_URL") or ""
    token = args.token or os.environ.get("OPENGRAB_TOKEN") or ""
    if not url or not token:
        from .settings import load_settings
        s = load_settings()
        host = s.api_host if s.api_host not in ("0.0.0.0", "::", "") else "127.0.0.1"
        url = url or f"http://{host}:{s.api_port}{s.base_path or ''}"
        token = token or s.api_token
    return url.rstrip("/"), token


class Client:
    def __init__(self, base: str, token: str, opener=urllib.request.urlopen):
        self.base, self.token, self.opener = base, token, opener

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener(req, timeout=20) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            try:
                msg = json.loads(exc.read() or b"{}").get("error") or exc.reason
            except ValueError:
                msg = exc.reason
            raise ClientError(f"{msg} (HTTP {exc.code})", 1) from None
        except (urllib.error.URLError, OSError) as exc:
            raise ClientError(f"OpenGrab is not reachable at {self.base}: {getattr(exc, 'reason', exc)}", 3) from None
        return json.loads(raw or b"null")


def _human(n) -> str:
    if not n:
        return "-"
    n = float(n)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return "-"


def run(argv=None, out=sys.stdout, opener=urllib.request.urlopen) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    as_json = args.json or getattr(args, "json_sub", False)
    base, token = _endpoint(args)
    c = Client(base, token, opener)

    def emit(obj, text_lines):
        if as_json:
            out.write(json.dumps(obj, indent=2, default=str) + "\n")
        else:
            for line in text_lines:
                out.write(line + "\n")

    try:
        if args.command == "add":
            if args.review:
                res = c.call("POST", "/api/linkgrabber", {"urls": args.urls, "source": "cli"})
                emit(res, [f"{res['added']} link(s) wait in the LinkGrabber"])
            else:
                res = c.call("POST", "/api/urls", {"urls": args.urls, "queue": args.queue, "hash": args.hash,
                                                   "auto_download": not args.no_start})
                failed = [r for r in res if r.get("error")]
                emit(res, [f"added {r['id']}  {r['url']}" for r in res if not r.get("error")]
                     + [f"error {r['url']}: {r['error']}" for r in failed])
                return 1 if failed else 0
        elif args.command == "list":
            links = c.call("GET", "/api/links")
            if args.status:
                links = [l for l in links if l["status"] == args.status]
            emit(links, [f"{l['id']:<14} {l['status']:<11} {round((l.get('progress') or 0) * 100):>3}%  "
                         f"{_human(l.get('size')):>9}  {l.get('title') or l.get('filename') or l['url']}" for l in links]
                 or ["no downloads"])
        elif args.command == "status":
            stats = c.call("GET", "/api/stats")
            flow = c.call("GET", "/api/flow")
            counts = {k: v for k, v in stats["counts"].items() if v}
            emit({"stats": stats, "flow": flow},
                 [f"speed {_human(stats['speed_bps'])}/s, {stats['total']} download(s): "
                  + (", ".join(f"{v} {k}" for k, v in counts.items()) or "none"),
                  f"speed profile {flow['active_profile']}, limit "
                  + (_human(flow['effective_limit_bps']) + "/s" if flow['effective_limit_bps'] else "none")])
        elif args.command == "notifications":
            items = c.call("GET", "/api/notifications")
            emit(items, [f"{'*' if not n['read'] else ' '} {n['title']}: {n['message']}" for n in items]
                 or ["no notifications"])
        else:
            ids = args.ids
            if ids == ["all"]:
                links = c.call("GET", "/api/links")
                wanted = {"pause": {"downloading", "waiting"}, "resume": {"paused"}, "start": {"waiting", "error", "paused"},
                          "stop": {"downloading", "waiting", "resolving"},
                          "remove": {"finished", "error", "skipped"}}[args.command]
                ids = [l["id"] for l in links if l["status"] in wanted]
            done = []
            for lid in ids:
                c.call("POST", f"/api/links/{lid}/{args.command}", {})
                done.append(lid)
            emit({"command": args.command, "ids": done}, [f"{args.command}: {len(done)} download(s)"])
        return 0
    except ClientError as exc:
        if as_json:
            out.write(json.dumps({"error": str(exc)}) + "\n")
        else:
            sys.stderr.write("opengrab: " + str(exc) + "\n")
        return exc.code
