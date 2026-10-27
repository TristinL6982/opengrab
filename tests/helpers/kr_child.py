import os
import sys
import time
import json

# Add the repo's src to Python path
repo_src = os.path.join(os.path.dirname(__file__), '..', 'src')
sys.path.insert(0, repo_src)

from opengrab.engine.downloader import Manager

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
    m = Manager(
        download_dir=dldir,
        per_link_chunks=4,
        max_parallel=1,
        speed_limit_kbps=20480
    )
    
    url = sys.argv[4]  # Should be passed after datadir and dldir
    m.add_url(url)

    while True:
        progress = m.get_overall_progress()
        print(f"PROGRESS {progress}", flush=True)
        time.sleep(0.2)  # Poll every 0.2 seconds

def resume_download(datadir, dldir):
    # Create Manager
    m = Manager(
        download_dir=dldir,
        per_link_chunks=4,
        max_parallel=1,
        speed_limit_kbps=20480
    )
    
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
        if all(m.is_link_finished(lid) or m.is_link_error(lid) for lid in [l["id"] for l in links]):
            break
        time.sleep(1)

    # Check final status
    finished = 0
    errored = 0
    result_path = None

    for link_info in links:
        if m.is_link_finished(link_info["id"]):
            finished += 1
            result_path = os.path.join(dldir, m.get_link_filename(link_info["id"]))
        elif m.is_link_error(link_info["id"]):
            errored += 1

    if finished == 1 and errored == 0:
        status = "finished"
    else:
        status = "error"

    print(f"RESULT {status} {result_path}")
    sys.exit(0)

if __name__ == '__main__':
    main()