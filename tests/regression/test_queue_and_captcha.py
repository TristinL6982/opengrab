import pytest
from tests.regression.conftest import run_repro

# Run misc.py once and reuse its output
misc_output = run_repro("misc.py").stdout

@pytest.mark.xfail(strict=True, reason="C6: Remove link deletes the downloaded file")
def test_c6_remove_link_keeps_file():
    for line in misc_output.splitlines():
        if line.startswith("A. after 'Remove this link?':"):
            assert "file still exists: True" in line
            return
    assert False, "Line not found"


@pytest.mark.xfail(strict=True, reason="B1: generic page with file links ends in ERROR")
def test_b1_generic_page_with_links_not_error():
    for line in misc_output.splitlines():
        if line.startswith("B. page with 2 file links"):
            assert "status: error" not in line
            return
    assert False, "Line not found"


def test_c8_pause_frees_slot():
    for line in misc_output.splitlines():
        if line.startswith("C. new link 6s after pausing"):
            assert line.endswith("waiting") is False
            return
    assert False, "Line not found"


@pytest.mark.xfail(strict=True, reason="B3: manual solver always first, paid solvers never reached")
def test_b3_paid_solver_before_manual():
    for line in misc_output.splitlines():
        if line.startswith("D. auto chain order for turnstile"):
            assert "['manual'," not in line
            return
    assert False, "Line not found"