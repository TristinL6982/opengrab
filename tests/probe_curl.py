import sys
sys.path.insert(0, r"C:\Users\trist\opengrab\src")
from curl_cffi import requests as cr

s = cr.Session(impersonate="chrome")
r = s.get("https://speed.hetzner.de/10MB.bin",
          headers={"Range": "bytes=0-1023"}, timeout=20, stream=True)
print("status:", r.status_code)
print("headers:", dict(r.headers))
chunks = list(r.iter_content(chunk_size=512))
print("chunks:", len(chunks), "bytes:", sum(len(c) for c in chunks))
print("url:", str(r.url))

# HEAD support
h = s.head("https://speed.hetzner.de/10MB.bin", timeout=20)
print("HEAD status:", h.status_code, "cl:", h.headers.get("content-length"),
      "accept-ranges:", h.headers.get("accept-ranges"))
