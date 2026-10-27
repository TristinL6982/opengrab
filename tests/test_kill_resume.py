import pytest
import os
import sys
import time
import subprocess
import hashlib
from tempfile import mkdtemp
import socket

# Add repo src to Python path
repo_src = os.path.join(os.path.dirname(__file__), '..', 'src')
sys.path.insert(0, repo_src)

from tests.helpers.range_server import serve

def test_kill_resume():
    # Create a 200 MiB file of random bytes
    tmpdir = mkdtemp()
    test_file = os.path.join(tmpdir, "test_200mb.bin")
    chunk_size = 1024 * 1024  # 1 MiB
    total_size = 200 * chunk_size
    sha256_hash = hashlib.sha256()

    with open(test_file, 'wb') as f:
        for i in range(200):
            block = os.urandom(chunk_size)
            f.write(block)
            sha256_hash.update(block)
    
    source_sha256 = sha256_hash.hexdigest()
    
    # Start range server on a free port
    import sys
    from tests.helpers.range_server import serve
    
    # Bind to localhost:0 to get an ephemeral port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    server = serve(test_file, port)
    
    try:
        url = f"http://127.0.0.1:{port}/test_200mb.bin"
        
        # Create data and download directories
        datadir = os.path.join(tmpdir, "data")
        dldir = os.path.join(tmpdir, "dl")
        os.makedirs(datadir, exist_ok=True)
        os.makedirs(dldir, exist_ok=True)

        # Spawn child 1 (start) -- uses kr_child.py with a subprocess
        proc = subprocess.Popen([sys.executable, "-u", 
                                os.path.join(os.path.dirname(__file__), "helpers", "kr_child.py"),
                                "start", datadir, dldir, url],
                                stdout=subprocess.PIPE,
                                text=True,
                                env=dict(os.environ, PYTHONPATH=repo_src))

        try:
            # Read progress until >= 0.40 or timeout
            timeout = 120
            start_time = time.time()
            
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                
                if line.startswith("PROGRESS "):
                    progress = float(line.split()[1])
                    if progress >= 0.40:
                        # Now sleep a bit to ensure progress has been saved
                        time.sleep(1.5)
                        proc.kill()  # Hard kill the process (like Task Manager)
                        proc.wait()
                        break
                
                if time.time() - start_time > timeout:
                    proc.kill()
                    proc.wait()
                    assert False, "Timeout waiting for progress >= 0.40"
                
        except Exception as e:
            proc.kill()
            proc.wait()
            raise e

        # Assert links.json exists and has exactly one link
        links_json_path = os.path.join(datadir, "links.json")
        assert os.path.exists(links_json_path)
        
        with open(links_json_path) as f:
            import json
            links = json.load(f)
            assert len(links) == 1

        # Now spawn child 2 (resume)
        result = subprocess.run([
                sys.executable,
                "-u",
                os.path.join(os.path.dirname(__file__), "helpers", "kr_child.py"),
                "resume",
                datadir,
                dldir
            ],
            timeout=360,
            stdout=subprocess.PIPE,
            text=True,
            env=dict(os.environ, PYTHONPATH=repo_src)
        )

        assert result.returncode == 0

        # Parse the RESULT line and extract status and path
        output = result.stdout.strip()
        assert output.startswith("RESULT ")

        parts = output.split()
        assert len(parts) == 3
        
        status = parts[1]
        result_path = parts[2]

        assert status == "finished"

        # Verify the downloaded file is correct
        assert os.path.exists(result_path)

        # Compute sha256 of downloaded file
        download_sha256 = hashlib.sha256()
        with open(result_path, 'rb') as f:
            while True:
                data = f.read(8192)
                if not data:
                    break
                download_sha256.update(data)
        
        assert download_sha256.hexdigest() == source_sha256

        # Assert size is correct
        assert os.path.getsize(result_path) == total_size

        # Assert no .part or .part.json files remain in directory
        for file in os.listdir(dldir):
            assert not file.endswith('.part')
            assert not file.endswith('.part.json')
    finally:
        server.shutdown()
        server.server_close()

if __name__ == "__main__":
    test_kill_resume()