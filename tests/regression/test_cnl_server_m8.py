import base64
from Cryptodome.Cipher import AES
from opengrab.cnl_server import make_server, JDCHECK_JS
from opengrab.cnl import decode_addcrypted2

def test_get_jdcheck_js():
    """Test GET /jdcheck.js and GET /jdcheck.js?x=1"""
    got = []
    
    # Test the endpoint directly with a mock server
    server = make_server(got.append, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    try:
        import urllib.request
        import urllib.parse
        
        # Test GET /jdcheck.js
        req = urllib.request.Request("http://127.0.0.1:%d/jdcheck.js" % server.server_address[1])
        response = urllib.request.urlopen(req, timeout=5)
        assert response.getcode() == 200
        assert response.read().decode('utf-8') == JDCHECK_JS
        
        # Test GET /jdcheck.js?x=1
        req = urllib.request.Request("http://127.0.0.1:%d/jdcheck.js?x=1" % server.server_address[1])
        response = urllib.request.urlopen(req, timeout=5)
        assert response.getcode() == 200
        assert response.read().decode('utf-8') == JDCHECK_JS
        
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

def test_get_flash():
    """Test GET / and GET /flash"""
    got = []
    
    # Test the endpoint directly with a mock server
    server = make_server(got.append, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    try:
        import urllib.request
        
        # Test GET /
        req = urllib.request.Request("http://127.0.0.1:%d/" % server.server_address[1])
        response = urllib.request.urlopen(req, timeout=5)
        assert response.getcode() == 200
        assert response.read().decode('utf-8') == "JDownloader"
        
        # Test GET /flash/
        req = urllib.request.Request("http://127.0.0.1:%d/flash/" % server.server_address[1])
        response = urllib.request.urlopen(req, timeout=5)
        assert response.getcode() == 200
        assert response.read().decode('utf-8') == "JDownloader"
        
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

def test_post_flash_add():
    """Test POST /flash/add"""
    got = []
    
    # Test the endpoint directly with a mock server
    server = make_server(got.append, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    try:
        import urllib.request
        import urllib.parse
        
        # Test POST with valid URLs
        data = urllib.parse.urlencode({
            "urls": "https://a.com/1\r\nhttps://a.com/2",
            "package": "P"
        }).encode('utf-8')
        
        req = urllib.request.Request("http://127.0.0.1:%d/flash/add" % server.server_address[1], data=data)
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        response = urllib.request.urlopen(req, timeout=5)
        assert response.getcode() == 200
        assert response.read().decode('utf-8') == "success\r\n"
        assert len(got) == 1
        assert got[0]["links"] == ["https://a.com/1", "https://a.com/2"]
        assert got[0]["passwords"] == []
        assert got[0]["source"] == ""
        assert got[0]["package"] == "P"
        
        # Test POST with empty URLs
        got.clear()
        data = urllib.parse.urlencode({
            "urls": "",
        }).encode('utf-8')
        
        req = urllib.request.Request("http://127.0.0.1:%d/flash/add" % server.server_address[1], data=data)
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        response = urllib.request.urlopen(req, timeout=5)
        assert response.getcode() == 400
        assert response.read().decode('utf-8') == "failed\r\n"
        assert len(got) == 0
        
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

def test_post_flash_addcrypted2():
    """Test POST /flash/addcrypted2"""
    got = []
    
    # Create valid test data
    key = bytes.fromhex("31323334353637383930313233343536")
    plain = b"https://x.com/f1"
    pad_length = 16 - (len(plain) % 16)
    padded = plain + b"\x00" * pad_length
    crypted = base64.b64encode(AES.new(key, AES.MODE_CBC, iv=key).encrypt(padded)).decode("ascii")
    
    # Test the endpoint directly with a mock server
    server = make_server(got.append, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    try:
        import urllib.request
        import urllib.parse
        
        # Valid form data
        data = urllib.parse.urlencode({
            "jk": "function f(){ return '31323334353637383930313233343536';}",
            "crypted": crypted
        }).encode('utf-8')
        
        req = urllib.request.Request("http://127.0.0.1:%d/flash/addcrypted2" % server.server_address[1], data=data)
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        response = urllib.request.urlopen(req, timeout=5)
        assert response.getcode() == 200
        assert response.read().decode('utf-8') == "success\r\n"
        assert len(got) == 1
        assert got[0]["links"] == ["https://x.com/f1"]
        
        # Invalid form data - bad key
        got.clear()
        data = urllib.parse.urlencode({
            "jk": "function f(){ return 'zz';}",
            "crypted": "AAAA"
        }).encode('utf-8')
        
        req = urllib.request.Request("http://127.0.0.1:%d/flash/addcrypted2" % server.server_address[1], data=data)
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        response = urllib.request.urlopen(req, timeout=5)
        assert response.getcode() == 400
        assert response.read().decode('utf-8') == "failed\r\n"
        assert len(got) == 0
        
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

def test_not_found():
    """Test that non-existent paths return 404"""
    got = []
    
    # Test the endpoint directly with a mock server
    server = make_server(got.append, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    try:
        import urllib.request
        
        req = urllib.request.Request("http://127.0.0.1:%d/nope" % server.server_address[1])
        response = urllib.request.urlopen(req, timeout=5)
        assert response.getcode() == 404
        assert response.read().decode('utf-8') == ""
        
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

def test_real_server():
    """End-to-end test with real server and HTTP client"""
    got = []
    
    # Test the endpoint directly with a mock server
    server = make_server(got.append, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    try:
        import urllib.request
        import urllib.parse
        
        data = urllib.parse.urlencode({
            "urls": "https://c.com/z"
        }).encode('utf-8')
        
        req = urllib.request.Request("http://127.0.0.1:%d/flash/add" % server.server_address[1], data=data)
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
        response = urllib.request.urlopen(req, timeout=5)
        assert response.getcode() == 200
        assert response.read().decode('utf-8') == "success\r\n"
        
        # Check the headers
        assert response.headers['Access-Control-Allow-Origin'] == "*"
        
        # Verify that got was updated correctly
        assert len(got) == 1
        assert got[0]["links"] == ["https://c.com/z"]
        
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)