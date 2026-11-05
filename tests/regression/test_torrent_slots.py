"""Torrents run inside libtorrent: they never use up HTTP download slots, and a paused
link of any kind never holds one."""
import os
import time

import pytest

from opengrab.models import Link, LinkStatus
from tests.helpers.apiserver import req, running_server
from tests.helpers.filesite import FileSite

BLOB = os.urandom(64 * 1024)


@pytest.fixture
def site():
    s = FileSite()
    yield s
    s.close()


def _wait(pred, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


def _fake_torrent(srv, status, queue=""):
    extra = {"torrent": {"info_hash": "", "magnet": "magnet:?xt=urn:btih:" + "a" * 40,
                         "torrent_file": "", "save_path": ""}}
    if queue:
        extra["queue"] = queue
    link = Link(url="magnet:?xt=urn:btih:" + "a" * 40, plugin="torrent", status=status, extra=extra)
    with srv.manager._lock:
        srv.manager._links[link.id] = link
    return link


@pytest.mark.parametrize("max_parallel", [1, 2])
def test_idle_and_paused_torrents_do_not_block_http_downloads(site, max_parallel):
    url = site.add("/a.bin", BLOB)
    with running_server({"max_parallel": max_parallel}) as srv:
        _fake_torrent(srv, LinkStatus.DOWNLOADING)  # 0 B/s, "downloading"
        _fake_torrent(srv, LinkStatus.PAUSED)
        st, out = req(srv, "POST", "/api/urls", body={"urls": [url]})
        lid = out[0]["id"]
        _wait(lambda: srv.manager.get(lid).status.value == "finished")


def test_torrents_in_a_full_named_queue_do_not_block_http_downloads(site):
    url = site.add("/b.bin", BLOB)
    with running_server({"max_parallel": 3}) as srv:
        srv.manager.flow.queues.create("Night", max_active=2)
        _fake_torrent(srv, LinkStatus.DOWNLOADING, queue="Night")
        _fake_torrent(srv, LinkStatus.DOWNLOADING, queue="Night")
        st, out = req(srv, "POST", "/api/urls", body={"urls": [url], "queue": "Night"})
        assert not out[0].get("error"), out
        lid = out[0]["id"]
        _wait(lambda: srv.manager.get(lid).status.value == "finished")


def test_paused_http_link_does_not_hold_a_slot(site):
    paused_url = site.add("/p.bin", BLOB)
    url = site.add("/c.bin", BLOB)
    with running_server({"max_parallel": 1}) as srv:
        link = srv.manager.add_url(paused_url, auto_download=False)
        srv.manager.pause_download(link.id)
        st, out = req(srv, "POST", "/api/urls", body={"urls": [url]})
        lid = out[0]["id"]
        _wait(lambda: srv.manager.get(lid).status.value == "finished")
