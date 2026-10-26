"""End-to-end API verification for OpenGrab (M1: server + engine + UI).

Boots a real DownloadManager + ApiServer on an ephemeral port in a fully
isolated environment (temp download dir + temp APPDATA), then drives the
HTTP API the way the web UI does:

  1.  /api/ping and static assets (UI shell) respond
  2.  a real 9,979-byte wheel downloads to the exact expected size + sha256
  3.  a 2.17 MB wheel is interrupted mid-transfer, the .part file survives,
      resume completes it to the exact expected size + sha256
  4.  the /api/events SSE stream delivers the hello event
  5.  state persists across an engine restart (links reload from disk)

Run:  PYTHONPATH=src .venv/Scripts/python.exe tests/e2e_api.py
Exits 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Isolate the app state (APPDATA) BEFORE any opengrab import touches it.
# ---------------------------------------------------------------------------
_TMP = Path(tempfile.mkdtemp(prefix="og-e2e-"))
_APPDATA = _TMP / "appdata"
_APPDATA.mkdir()
os.environ["APPDATA"] = str(_APPDATA)          # Windows app-data root
os.environ["APPDATA_OPENGRAB"] = str(_TMP)     # extra safety for app_data_dir

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import opengrab.server as server  # noqa: E402
from opengrab.engine.manager import DownloadManager  # noqa: E402
from opengrab.settings import Settings  # noqa: E402

# (url, expected_size, expected_sha256)
SMALL = (
    "https://files.pythonhosted.org/packages/b3/38/89ba8ad64ae25be8de66a6d4"
    "63314cf1eb366222074cfda9ee839c56a4b4/mdurl-0.1.2-py3-none-any.whl",
    9979,
    "84008a41e51615a49fc9966191ff91509e3c40b939176e643fd50a5c2196b8f8",
)
BIG = (
    "https://files.pythonhosted.org/packages/c5/17/8bbd530b8659e9d963f5b16f6c"
    "b8e159f4cd174b4d6304e48969ab12dba8/pydantic_core-2.50.1-cp312-cp312-"
    "win_amd64.whl",
    2172522,
    "132529c83901437ff642f585216831bf5fd7a91df66829907e155192ead62498",
)

FAILURES: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}" + (f"  -- {detail}" if detail and not ok else ""))
    (PASSES if ok else FAILURES).append(name)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    dl_dir = _TMP / "downloads"
    dl_dir.mkdir()
    settings = Settings(download_dir=str(dl_dir), max_parallel=2,
                        per_link_chunks=2)

    mgr = DownloadManager(settings)
    mgr.start()
    srv = server.ApiServer(settings, mgr, host="127.0.0.1", port=0)
    port = srv.start()
    base = f"http://127.0.0.1:{port}"
    print(f"server on {base}  (downloads: {dl_dir})")

    def req(method: str, path: str, body=None, timeout=60):
        r = urllib.request.Request(base + path, method=method)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            r.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(r, data, timeout=timeout) as resp:
            return resp.status, resp.read()

    def links():
        _, raw = req("GET", "/api/links")
        return json.loads(raw)

    def wait_status(link_id: str, statuses, timeout=180):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            for l in links():
                if l["id"] == link_id:
                    last = l
                    if l["status"] in statuses:
                        return l
            time.sleep(0.5)
        return last

    def add(url):
        _, raw = req("POST", "/api/urls", {"urls": [url], "auto_download": True})
        return json.loads(raw)[0]

    try:
        # ---- 1. ping + static assets ------------------------------------
        _, raw = req("GET", "/api/ping")
        ping = json.loads(raw)
        check("api: /api/ping", ping.get("ok") is True and
              ping.get("app") == "OpenGrab", str(ping))

        for asset in ["/", "/app.js", "/style.css", "/sw.js",
                      "/manifest.webmanifest", "/opengrab.svg"]:
            status, body = req("GET", asset)
            check(f"ui asset: GET {asset}", status == 200 and len(body) > 50,
                  f"status={status} len={len(body)}")

        # ---- 2. small file: full download + integrity -------------------
        url, size, digest = SMALL
        l1 = add(url)
        fin = wait_status(l1["id"], {"finished"}, timeout=120)
        check("small: finished", fin is not None and fin["status"] == "finished",
              f"status={fin and fin.get('status')} err={fin and fin.get('error')}")
        fpath = (dl_dir / Path(fin["filename"]).name) if fin and fin.get("filename") else None
        if fpath is None:
            cands = list(dl_dir.glob("*.whl"))
            fpath = cands[0] if cands else None
        ok = (fpath is not None and fpath.exists()
              and fpath.stat().st_size == size and sha256_of(fpath) == digest)
        check("small: size+sha256 verified", ok,
              f"path={fpath} size={fpath.stat().st_size if fpath and fpath.exists() else '?'}")

        # ---- 3. big file: interrupt mid-transfer, resume, verify --------
        burl, bsize, bdigest = BIG
        l2 = add(burl)
        deadline = time.time() + 120
        interrupted = False
        while time.time() < deadline:
            l = next((x for x in links() if x["id"] == l2["id"]), None)
            if l and l["status"] == "downloading" and (l.get("downloaded") or 0) > 100_000:
                req("POST", f"/api/links/{l2['id']}/stop")
                interrupted = True
                break
            if l and l["status"] in ("finished", "error"):
                break
            time.sleep(0.2)
        check("resume: interrupted mid-transfer", interrupted,
              "download finished before we could stop it")

        if interrupted:
            paused = wait_status(l2["id"], {"paused"}, timeout=60)
            check("resume: status paused after stop",
                  paused is not None and paused["status"] == "paused",
                  str(paused and paused.get("status")))
            parts = list(dl_dir.glob("*.part"))
            part = parts[0] if parts else None
            has_bytes = part is not None and part.stat().st_size > 0
            check("resume: .part file kept on disk", has_bytes,
                  f"parts={[p.name for p in parts]}")

            req("POST", f"/api/links/{l2['id']}/resume")
            fin2 = wait_status(l2["id"], {"finished"}, timeout=300)
            check("resume: finished after resume",
                  fin2 is not None and fin2["status"] == "finished",
                  f"status={fin2 and fin2.get('status')} err={fin2 and fin2.get('error')}")
            fpath2 = (dl_dir / Path(fin2["filename"]).name) if fin2 and fin2.get("filename") else None
            if fpath2 is None:
                cands = [c for c in dl_dir.glob("*.whl") if c.stat().st_size > 100_000]
                fpath2 = cands[0] if cands else None
            ok2 = (fpath2 is not None and fpath2.exists()
                   and fpath2.stat().st_size == bsize
                   and sha256_of(fpath2) == bdigest)
            check("resume: final size+sha256 verified", ok2,
                  f"size={fpath2.stat().st_size if fpath2 and fpath2.exists() else '?'}")

        # ---- 4. SSE event stream ----------------------------------------
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        conn.request("GET", "/api/events")
        resp = conn.getresponse()
        got = []
        for _ in range(6):
            line = resp.fp.readline()   # SSE is line-oriented; read(amt) would
            if not line:                # wait for EOF on a keep-alive 1.1 stream
                break
            got.append(line.decode("utf-8", "replace").strip())
            if not got[-1] and len(got) >= 3:
                break
        conn.close()
        first = "\n".join(got)
        check("events: SSE hello delivered",
              resp.status == 200 and "hello" in first, first[:120])

        # ---- 5. persistence across engine restart -----------------------
        mgr.shutdown()
        srv.stop()

        mgr2 = DownloadManager(settings)
        mgr2.start()
        reloaded = [l for l in mgr2.all_links()
                    if l.url == url or l.url == burl]
        check("persist: links reload from disk", len(reloaded) >= 1,
              f"reloaded={len(reloaded)}")
        mgr2.shutdown()
    finally:
        try:
            srv.stop()
        except Exception:
            pass
        try:
            mgr.shutdown()
        except Exception:
            pass

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("FAILED:", *FAILURES, sep="\n  - ")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
