"""A started and stopped server must not leave its background threads running.

The suite starts hundreds of servers in one process; threads left behind by each
one add up and exhaust the per-process thread limit on macOS.
"""
import threading
import time

from tests.helpers.apiserver import running_server


def _settle(limit, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if threading.active_count() <= limit:
            return True
        time.sleep(0.1)
    return threading.active_count() <= limit


def test_running_server_stop_returns_threads_to_baseline():
    with running_server():  # warm up lazy, process-wide helper threads
        pass
    _settle(0)
    baseline = threading.active_count()
    for _ in range(3):
        with running_server():
            assert threading.active_count() > baseline
    assert _settle(baseline + 1), sorted(t.name for t in threading.enumerate())
