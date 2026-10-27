"""Repro: stop a multi-chunk download mid-way, restart it, check integrity."""
import hashlib, os, re, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
tmp = Path(tempfile.mkdtemp()); os.environ["OPENGRAB_DATA_DIR"] = str(tmp/"data")
from opengrab.settings import Settings
from opengrab.engine.manager import DownloadManager
from opengrab.models import LinkStatus
DATA = os.urandom(8 * 1024 * 1024); SHA = hashlib.sha256(DATA).hexdigest()
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _hdr(self, code, s, e):
        self.send_response(code); self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(e - s + 1))
        if code == 206: self.send_header("Content-Range", f"bytes {s}-{e}/{len(DATA)}")
        self.send_header("Content-Type", "application/octet-stream"); self.end_headers()
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
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/blob.bin"
st = Settings(download_dir=str(tmp/"dl"), per_link_chunks=4, max_parallel=1)
m = DownloadManager(st); m.start()
link = m.add_url(url)
t1=time.time()
while m.get(link.id).status != LinkStatus.DOWNLOADING:
    time.sleep(0.05)
    if time.time()-t1>15: print("stuck:", m.get(link.id).model_dump(include={"status","error","plugin","direct_url","progress"})); sys.exit(2)
while (m.get(link.id).progress or 0) < 0.3: time.sleep(0.05)
print("progress before stop:", round(m.get(link.id).progress, 2))
m.stop_download(link.id)
time.sleep(3)
l=m.get(link.id); print("status 3s after stop:", l.status.value, "| path:", l.path, "| progress:", l.progress)
if l.status == LinkStatus.FINISHED:
    f=Path(l.path); b=f.read_bytes()
    print("'finished' file size:", len(b), "sha256 match:", hashlib.sha256(b).hexdigest()==SHA, "| zero 64KiB blocks:", sum(1 for i in range(0,len(b),65536) if b[i:i+65536]==bytes(65536)), "of", len(b)//65536)
    sys.exit(1)
part = tmp/"dl"/"blob.bin.part"
print("status after stop:", m.get(link.id).status.value, "| .part size:", part.stat().st_size, "of", len(DATA))
m.start_download(link.id)
t0 = time.time()
while m.get(link.id).status not in (LinkStatus.FINISHED, LinkStatus.ERROR) and time.time()-t0 < 60: time.sleep(0.05)
l = m.get(link.id); f = Path(l.path)
got = hashlib.sha256(f.read_bytes()).hexdigest()
zeros = sum(1 for i in range(0, len(DATA), 65536) if f.read_bytes()[i:i+65536] == bytes(65536))
print("status after restart:", l.status.value, "in %.2fs" % (time.time()-t0), "| size:", f.stat().st_size)
print("sha256 match:", got == SHA, "| all-zero 64KiB blocks:", zeros)
sys.exit(0 if got == SHA else 1)
