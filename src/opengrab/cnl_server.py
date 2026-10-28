import http.server
import socketserver
import urllib.parse
import threading
from .cnl import decode_addcrypted2, CnlError, parse_links

JDCHECK_JS = "jdownloader=true; var version='17461';"

def handle_cnl(method: str, path: str, form: dict, on_links) -> tuple[int, str]:
    # Normalize path by removing query and trailing slash (but not the root)
    normalized_path = path.split('?')[0]
    if normalized_path != "/" and normalized_path.endswith("/"):
        normalized_path = normalized_path[:-1]
    
    if method == "GET":
        if normalized_path == "/jdcheck.js":
            return (200, JDCHECK_JS)
        elif normalized_path in ["/", "/flash"]:
            return (200, "JDownloader")
        else:
            return (404, "")
    
    elif method == "POST":
        if normalized_path == "/flash/add":
            links = parse_links(form.get("urls", ""))
            if not links:
                return (400, "failed\r\n")
            result = {
                "links": links,
                "passwords": [],
                "source": form.get("source", ""),
                "package": form.get("package", "")
            }
            on_links(result)
            return (200, "success\r\n")
        
        elif normalized_path == "/flash/addcrypted2":
            try:
                result = decode_addcrypted2(form)
            except CnlError:
                return (400, "failed\r\n")
            on_links(result)
            return (200, "success\r\n")
        
        else:
            return (404, "")
    
    else:
        return (404, "")

class CNLHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, on_links=None, **kwargs):
        self.on_links = on_links
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        status, body = handle_cnl("GET", self.path, {}, self.on_links)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))
    
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        form = urllib.parse.parse_qs(post_data.decode("utf-8"), keep_blank_values=True)
        # Keep only the first value for each key
        simple_form = {k: v[0] if v else "" for k, v in form.items()}
        
        status, body = handle_cnl("POST", self.path, simple_form, self.on_links)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))
    
    def log_message(self, *args):
        # Do nothing to suppress logging
        pass

def make_server(on_links, host: str = "127.0.0.1", port: int = 9666):
    """Create a ThreadingHTTPServer for the CNL endpoint."""
    server = socketserver.ThreadingTCPServer((host, port), 
                                           lambda *args, **kwargs: CNLHandler(*args, on_links=on_links, **kwargs))
    return server