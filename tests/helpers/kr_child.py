import os
import sys
import time
import json

# Add the repo's src to Python path
repo_src = os.path.join(os.path.dirname(__file__), '..', 'src')
sys.path.insert(0, repo_src)

def main():
    # Configure os.environ before importing opengrab
    assert len(sys.argv) >= 4, "Usage: kr_child.py {start|resume} <datadir> <dldir>"
    
    action = sys.argv[1]
    datadir = sys.argv[2]
    dldir = sys.argv[3]

    os.environ["OPENGRAB_DATA_DIR"] = datadir

    if action == "start":
        start_download(datadir, dldir)
    elif action == "resume":
        resume_download(datadir, dldir)
    else:
        raise ValueError(f"Invalid action: {action}")

def start_download(datadir, dldir):
    from opengrab.settings import Settings
    from opengrab.engine.manager import DownloadManager
    
    s = Settings(download_dir=dldir, per_link_chunks=4, max_parallel=1, speed_limit_kbps=20480)
    m = DownloadManager(s)
    
    url = sys.argv[4]  # Should be passed after datadir and dldir
    link = m.add_url(url)
    
    while True:
        progress = link.progress if link else 0.0
        print(f"PROGRESS {progress}", flush=True)
        time.sleep(0.2)  # Poll every 0.2 seconds

def resume_download(datadir, dldir):
    from opengrab.settings import Settings
    from opengrab.engine.manager import DownloadManager
    from opengrab.models import LinkStatus
    
    s = Settings(download_dir=dldir, per_link_chunks=4, max_parallel=1, speed_limit_kbps=20480)
    m = DownloadManager(s)
    
    # Start manager
    m.start()
    
    # Resume each link in the data file
    links_json_path = os.path.join(datadir, "links.json")
    with open(links_json_path) as f:
        links = json.load(f)
    
    for link_info in links:
        link_id = link_info["id"]
        m.resume_download(link_id)
        m.start_download(link_id)
    
    # Wait up to 300 seconds for any download to finish or error
    timeout = 300
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        if all(m.get(link_id).status in (LinkStatus.FINISHED, LinkStatus.ERROR) for link_id in [l["id"] for l in links]):
            break
        time.sleep(1)
    
    # Check final status
    finished = 0
    errored = 0
    result_path = None
    
    for link_info in links:
        link = m.get(link_info["id"])
        if link and link.status == LinkStatus.FINISHED:
            finished += 1
            result_path = os.path.join(dldir, link.filename)
        elif link and link.status == LinkStatus.ERROR:
            errored += 1
    
    if finished == 1 and errored == 0:
        status = "finished"
    else:
        status = "error"
    
    print(f"RESULT {status} {result_path}")
    m.shutdown()
    sys.exit(0)

if __name__ == '__main__':
    main()