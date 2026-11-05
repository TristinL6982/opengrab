"""Torrent stats, the peers view and the offline country lookup.

No libtorrent and no network: the status and the handle are fakes, and the
country database is a tiny CSV written by the test."""
import gzip
from pathlib import Path
from types import SimpleNamespace as NS

from opengrab import geoip
from opengrab.engine.torrents import MAX_PEERS, peer_rows, torrent_stats
from opengrab.models import Link, LinkStatus
from tests.helpers.apiserver import req, running_server

STATIC = Path(__file__).resolve().parents[2] / "src" / "opengrab" / "ui" / "static"

CSV = """0.0.0.0,0.255.255.255,ZZ
1.0.0.0,1.0.0.255,AU
8.8.8.0,8.8.8.255,US
10.0.0.0,10.0.0.0,XX
81.2.69.0,81.2.69.255,GB
2001:db8::,2001:db8:ffff:ffff:ffff:ffff:ffff:ffff,DE
2a00::,2a00:ffff:ffff:ffff:ffff:ffff:ffff:ffff,FR
"""

# libtorrent's peer_info flag attributes are the bit values themselves
BITS = dict(seed=0x2, rc4_encrypted=0x10, plaintext_encrypted=0x20, local_connection=0x4, utp_socket=0x8)


def fake_status(**kw):
    base = dict(num_peers=7, num_seeds=2, list_seeds=40, list_peers=300, num_complete=55, num_incomplete=-1,
                download_rate=2048, upload_rate=512, all_time_upload=3000, total_wanted_done=1000,
                distributed_copies=2.5, state="downloading", has_metadata=True, paused=False,
                is_seeding=False, num_pieces=30)
    base.update(kw)
    return NS(**base)


def fake_peer(ip, port=6881, client=b"qBittorrent 4.6", flags=0, progress=0.5, down=0, up=0):
    return NS(ip=(ip, port), client=client, flags=flags, progress=progress, progress_ppm=int(progress * 1e6),
              payload_down_speed=down, payload_up_speed=up, down_speed=down, up_speed=up, **BITS)


def db_in(tmp_path):
    p = geoip.db_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(CSV, encoding="utf-8")
    return geoip.load_db(tmp_path)


# -- stats --------------------------------------------------------------------------
def test_stats_from_a_status_object():
    s = torrent_stats(fake_status(), total_pieces=120, eta=90.0)
    assert (s["seeds"], s["peers"]) == (2, 7)                       # old keys unchanged
    assert (s["connected_seeds"], s["connected_peers"]) == (2, 5)   # peers excluding seeds
    assert (s["swarm_seeds"], s["swarm_peers"]) == (40, 300)
    assert s["tracker_seeds"] == 55 and s["tracker_peers"] is None  # -1 means the tracker did not say
    assert (s["download_rate"], s["upload_rate"], s["uploaded"]) == (2048, 512, 3000)
    assert s["ratio"] == 3.0 and s["copies"] == 2.5
    assert (s["pieces_done"], s["pieces_total"], s["eta"], s["state"]) == (30, 120, 90.0, "downloading")


def test_stats_state_names():
    assert torrent_stats(fake_status(state="checking_files"))["state"] == "checking"
    assert torrent_stats(fake_status(state="downloading_metadata", has_metadata=False))["state"] == "metadata"
    assert torrent_stats(fake_status(state="seeding", is_seeding=True))["state"] == "seeding"
    assert torrent_stats(fake_status(paused=True))["state"] == "paused"
    s = torrent_stats(fake_status(distributed_copies=-1, total_wanted_done=0))
    assert s["copies"] is None and s["ratio"] == 0.0


# -- peers ----------------------------------------------------------------------------
def test_peer_rows_shape_flags_and_cap():
    db = db_in(Path(__import__("tempfile").mkdtemp()))
    peers = [fake_peer("8.8.8.8", 51413, flags=BITS["rc4_encrypted"] | BITS["local_connection"], down=900,
                       progress=0.25),
             fake_peer("2001:db8::5", client="", flags=BITS["seed"] | BITS["utp_socket"], progress=1.0, up=100),
             fake_peer("192.168.1.9")]
    rows = peer_rows(peers, db)
    first = rows[0]
    assert (first["ip"], first["port"], first["client"], first["country"]) == ("8.8.8.8", 51413, "qBittorrent 4.6", "US")
    assert first["down_speed"] == 900 and first["progress"] == 25.0
    assert first["flags"] == ["encrypted", "outgoing"]
    v6 = next(r for r in rows if r["ip"] == "2001:db8::5")
    assert v6["flags"] == ["seed", "incoming", "utp"] and v6["country"] == "DE"
    assert next(r for r in rows if r["ip"] == "192.168.1.9")["country"] == ""
    many = [fake_peer(f"1.0.0.{i % 250}", 1000 + i) for i in range(MAX_PEERS + 50)]
    assert len(peer_rows(many, db)) == MAX_PEERS


def test_peers_api_with_a_fake_handle():
    handle = NS(h=NS(get_peer_info=lambda: [fake_peer("8.8.8.8", down=10), fake_peer("1.0.0.7"),
                                            fake_peer("1.0.0.8"), fake_peer("203.0.113.5")]))
    with running_server() as srv:
        link = Link(url="magnet:?xt=urn:btih:abc", plugin="torrent", status=LinkStatus.DOWNLOADING)
        srv.manager._links[link.id] = link
        srv.manager.torrents.handles[link.id] = handle
        # database missing: rows come back without a country and the API says so
        st, out = req(srv, "GET", f"/api/torrents/{link.id}/peers")
        assert st == 200 and out["total"] == 4 and out["geo"]["installed"] is False
        assert out["countries"] == [] and all(p["country"] == "" for p in out["peers"])
        assert set(out["peers"][0]) == {"ip", "port", "client", "down_speed", "up_speed", "progress", "flags", "country"}
        assert geoip.status()["installed"] is False
        # database installed: countries, the summary and the attribution
        p = geoip.db_path()
        assert srv.tmp in p.parents                      # the test's own data dir, never the real one
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(CSV, encoding="utf-8")
        st, out = req(srv, "GET", f"/api/torrents/{link.id}/peers")
        assert out["geo"] == {"installed": True, "attribution": "IP geolocation by DB-IP",
                              "attribution_url": "https://db-ip.com"}
        assert out["countries"][0] == {"country": "AU", "flag": geoip.flag("AU"), "count": 2}
        assert {c["country"] for c in out["countries"]} == {"AU", "US"}
        assert req(srv, "GET", "/api/torrents/nope/peers")[0] == 404
        link2 = Link(url="magnet:?xt=urn:btih:def", plugin="torrent")
        srv.manager._links[link2.id] = link2
        assert req(srv, "GET", f"/api/torrents/{link2.id}/peers")[0] == 409
        assert req(srv, "GET", "/api/geoip")[1]["installed"] is True


# -- country lookup ------------------------------------------------------------------------
def test_lookup_ipv4_and_ipv6_including_range_edges(tmp_path):
    db = db_in(tmp_path)
    assert len(db) == 7
    assert db.lookup("8.8.8.0") == "US" and db.lookup("8.8.8.255") == "US"      # both edges inside
    assert db.lookup("8.8.7.255") == "" and db.lookup("8.8.9.0") == ""          # one outside each edge
    assert db.lookup("1.0.0.0") == "AU" and db.lookup("81.2.69.200") == "GB"
    assert db.lookup("2001:db8::") == "DE" and db.lookup("2001:db8:ffff:ffff:ffff:ffff:ffff:ffff") == "DE"
    assert db.lookup("2001:db9::") == "" and db.lookup("2a00:1::1") == "FR"
    assert db.lookup("::ffff:8.8.8.8") == "US"                                   # IPv4-mapped IPv6
    assert db.lookup("0.1.2.3") == "" and db.lookup("10.0.0.0") == ""            # ZZ / XX mean unknown
    assert db.lookup("not an ip") == "" and db.lookup("") == ""


def test_missing_database(tmp_path):
    assert geoip.is_installed(tmp_path) is False
    assert geoip.load_db(tmp_path) is None
    assert geoip.status(tmp_path)["installed"] is False
    assert geoip.remove(tmp_path) is False


def test_flag_and_summary():
    assert geoip.flag("us") == "\U0001F1FA\U0001F1F8" and geoip.flag("") == "" and geoip.flag("USA") == ""
    s = geoip.summarize(["US", "DE", "US", "", "DE", "US", "FR"])
    assert [(c["country"], c["count"]) for c in s] == [("US", 3), ("DE", 2), ("FR", 1)]


def test_install_uses_the_injected_fetch_only(tmp_path):
    seen = []

    def fetch(url):
        seen.append(url)
        if len(seen) == 1:
            raise OSError("404 not published yet")        # current month missing: fall back one month
        return gzip.compress(CSV.encode())

    st = geoip.install(tmp_path, fetch=fetch, now=1790000000)   # 2026-09-21
    assert st["installed"] is True and len(seen) == 2
    assert "dbip-country-lite-2026-09" in seen[0] and "dbip-country-lite-2026-08" in seen[1]
    assert geoip.load_db(tmp_path).lookup("8.8.8.8") == "US"
    assert geoip.remove(tmp_path) is True and geoip.load_db(tmp_path) is None


# -- markup and script hooks -------------------------------------------------------------------
def test_ui_hooks():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for el in ("peers-dialog", "peers-body", "peers-countries", "peers-geo-install", "peers-attr", "peers-close"):
        assert f'id="{el}"' in html
    assert "IP geolocation by DB-IP" in html and "Install country lookup" in html
    assert 'id="peers-dialog" class="win" aria-labelledby="peers-title"' in html
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'sm("Peers", "torrent-peers")' in js and 'case "torrent-peers"' in js
    assert "/api/torrents/${peersFor}/peers" in js and "setInterval(refreshPeers, 2000)" in js
    assert "function torrentStatsNote" in js and "/api/geoip/install" in js
    assert "ip-api" not in js and "ipinfo" not in js                    # no online lookup of peer addresses
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".tstats" in css and ".ccount" in css and ".peers-wrap" in css
    zh = (STATIC / "i18n" / "zh-CN.json").read_text(encoding="utf-8")
    assert zh.endswith("\n") and '"Install country lookup"' in zh and '"IP geolocation by DB-IP"' in zh
