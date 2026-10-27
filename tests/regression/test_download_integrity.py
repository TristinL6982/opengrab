import tempfile

import pytest
from tests.regression.conftest import run_repro

@pytest.mark.xfail(strict=True, reason="C1: stopped multi-chunk download is marked finished with a corrupt file")
def test_c1_stop_keeps_file_unfinished():
    r = run_repro("resume_parallel.py")
    assert r.returncode == 0, r.stdout[-800:]

@pytest.mark.xfail(strict=True, reason="C2: resume after exit finishes instantly with a corrupt file")
def test_c2_resume_after_exit_is_intact():
    tmp_path = tempfile.mkdtemp(prefix="og-c2-")
    run_repro("restart.py", "graceful", tmp_path)
    r = run_repro("restart.py", "resume", tmp_path)
    assert "sha256 match: True" in r.stdout

@pytest.mark.xfail(strict=True, reason="C3: crash or kill mid-download drops the link from links.json")
def test_c3_crash_keeps_active_link():
    tmp_path = tempfile.mkdtemp(prefix="og-c3-")
    r = run_repro("restart.py", "crash", tmp_path)
    assert "entries after exit at ~35%: []" not in r.stdout and "entries after exit" in r.stdout