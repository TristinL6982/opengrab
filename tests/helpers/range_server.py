import os
import sys
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote

class RangeRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not hasattr(self, 'file_path'):
            self.send_error(404)
            return

        # Get the file size
        file_size = os.path.getsize(self.file_path)

        # Check for range header
        range_header = self.headers.get('Range')
        start, end = 0, file_size - 1

        if range_header:
            try:
                range_start, range_end = range_header.strip().split('=')[1].split('-')
                if range_start:
                    start = int(range_start)
                if range_end:
                    end = int(range_end)
                else:
                    end = file_size - 1
                if start > end or start >= file_size:
                    self.send_error(416)
                    return
            except (ValueError, IndexError):
                self.send_error(400)
                return

        # Prepare response
        content_range = f'bytes {start}-{end}/{file_size}'
        self.send_response(206)
        self.send_header('Content-Range', content_range)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()

        # Serve the content
        with open(self.file_path, 'rb') as f:
            f.seek(start)
            data = f.read(end - start + 1)
            self.wfile.write(data)

    def do_HEAD(self):
        if not hasattr(self, 'file_path'):
            self.send_error(404)
            return

        # Get the file size
        file_size = os.path.getsize(self.file_path)
        self.send_response(200)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(file_size))
        self.end_headers()

def serve(path, port):
    # Set up the server
    handler = RangeRequestHandler
    handler.file_path = path

    server = ThreadingHTTPServer(('localhost', port), handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    return server

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <file> <port>")
        sys.exit(1)

    file_path = sys.argv[1]
    port = int(sys.argv[2])

    serve(file_path, port)
    try:
        while True:
            pass
    except KeyboardInterrupt:
        print("Server stopped.")