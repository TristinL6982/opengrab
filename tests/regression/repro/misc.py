import os, sys, tempfile, threading, time, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
tmp = Path(tempfile.mkdtemp()); os.environ["OPENGRAB_DATA_DIR"] = str(tmp/"data")
from opengrab.settings import Settings
from opengrab.engine.manager import DownloadManager
from opengrab.models import LinkStatus
from opengrab.captcha.chain import SolverChain
from opengrab.captcha.base import CaptchaRequest
SMALL = b"x" * 5000
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_HEAD(self): self.do_GET(head=True)
    def do_GET(self, head=False):
        if self.path.startswith("/page"):
            body = b'<html><head><title>Files</title></head><body><a href="/f/a.zip">a</a> <a href="/f/b.zip">b</a></body></html>'
            ct = "text/html"
        elif self.path.startswith("/slow"):
            self.send_response(200); self.send_header("Content-Length", str(10_000_000)); self.send_header("Content-Type","application/octet-stream"); self.end_headers()
            if head: return
            try:
                for _ in range(1000): self.wfile.write(b"y"*10000); time.sleep(0.05)
            except Exception: pass
            return
        else:
            body = SMALL; ct = "application/octet-stream"
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.send_header("Content-Type", ct); self.end_headers()
        if not head: self.wfile.write(body)
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
def wait(m, lid, states, t=20):
    t0=time.time()
    while m.get(lid).status not in states and time.time()-t0<t: time.sleep(0.05)
    return m.get(lid)
st = Settings(download_dir=str(tmp/"dl"), max_parallel=1, per_link_chunks=1)
m = DownloadManager(st); m.start()
# A: remove link deletes downloaded file
a = m.add_url(base + "/f/keep.bin"); a = wait(m, a.id, {LinkStatus.FINISHED, LinkStatus.ERROR})
p = Path(a.path); print("A. finished:", a.status.value, "| file exists:", p.exists())
m.remove_link(a.id); print("A. after 'Remove this link?': file still exists:", p.exists())
# B: generic page with links
b = m.add_url(base + "/page.html"); b = wait(m, b.id, {LinkStatus.WAITING, LinkStatus.ERROR})
print("B. page with 2 file links -> status:", b.status.value, "| error:", b.error, "| children:", len(b.children or []))
# C: paused download blocks the only worker
c = m.add_url(base + "/slow.bin"); wait(m, c.id, {LinkStatus.DOWNLOADING}); time.sleep(0.5)
m.pause_download(c.id); print("C. paused slow link:", m.get(c.id).status.value)
d = m.add_url(base + "/f/next.bin"); time.sleep(6)
print("C. new link 6s after pausing another (max_parallel=1):", m.get(d.id).status.value)
# D: captcha chain in auto mode with a paid key configured
st2 = Settings(captcha_provider="auto", captcha_2captcha_key="dummy-key")
chain = SolverChain(st2)
print("D. auto chain order for turnstile:", [s.id for s in chain._ordered("turnstile")])
print("D. solve(turnstile) result:", chain.solve(CaptchaRequest(kind="turnstile", site_url="https://x", recaptcha_sitekey="0x4")).text)
os._exit(0)
