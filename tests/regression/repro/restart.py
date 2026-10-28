"""Repro: app exit mid-download (graceful shutdown vs crash), then resume in a new process."""
import hashlib, json, os, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
mode, tmp = sys.argv[1], Path(sys.argv[2]); os.environ["OPENGRAB_DATA_DIR"] = str(tmp/"data")
from opengrab.settings import Settings
from opengrab.engine.manager import DownloadManager
from opengrab.models import LinkStatus
import random; random.seed(7); DATA = random.randbytes(8*1024*1024); SHA = hashlib.sha256(DATA).hexdigest()
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _hdr(self, code, s, e):
        self.send_response(code); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(e - s + 1))
        if code == 206: self.send_header("Content-Range", f"bytes {s}-{e}/{len(DATA)}")
        self.end_headers()
    def do_HEAD(self): self._hdr(200, 0, len(DATA) - 1)
    def do_GET(self):
        m = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        s, e, code = 0, len(DATA) - 1, 200
        if m: s = int(m.group(1)); e = int(m.group(2)) if m.group(2) else e; code = 206
        self._hdr(code, s, e)
        try:
            for i in range(s, e + 1, 65536):
                self.wfile.write(DATA[i:min(i + 65536, e + 1)]); time.sleep(0.1)
        except Exception: pass
srv = ThreadingHTTPServer(("127.0.0.1", 18999), H); srv.allow_reuse_address=True
threading.Thread(target=srv.serve_forever, daemon=True).start()
st = Settings(download_dir=str(tmp/"dl"), per_link_chunks=4, max_parallel=1)
m = DownloadManager(st); m.start()
if mode in ("crash", "graceful"):
    link = m.add_url("http://127.0.0.1:18999/blob.bin")
    while (m.get(link.id).progress or 0) < 0.35: time.sleep(0.05)
    if mode == "graceful": m.shutdown()
    else: time.sleep(1.2)  # let debounced persist run
    from opengrab.sessiondb import SessionDB; _db = SessionDB(str(tmp/"data"/"session.db")); saved = [l["data"] for p in _db.load() if p["id"] == "queue" for l in p["links"]]; _db.close()
    print(f"[{mode}] links.json entries after exit at ~35%:", [(d["status"]) for d in saved])
    os._exit(0)
else:
    ls = m.all_links(); print("[resume] links loaded:", [(l.status.value) for l in ls])
    if not ls: sys.exit(3)
    m.resume_download(ls[0].id); t0=time.time()
    while m.get(ls[0].id).status not in (LinkStatus.FINISHED, LinkStatus.ERROR) and time.time()-t0<60: time.sleep(0.05)
    l=m.get(ls[0].id); b=Path(l.path).read_bytes()
    print("[resume] status:", l.status.value, "after %.2fs"%(time.time()-t0), "| sha256 match:", hashlib.sha256(b).hexdigest()==SHA,
          "| zero 64KiB blocks:", sum(1 for i in range(0,len(b),65536) if b[i:i+65536]==bytes(65536)))
    os._exit(0)
