"""Mega (mega.nz / mega.co.nz) and transfer.it plugins: offline tests.

No network: the Mega API is faked. Crypto is checked against fixtures we
encrypt ourselves with random keys, one real attribute vector taken from a
public Mega file, and an independent block-by-block MAC implementation.
"""
import json
import os
from urllib.parse import parse_qs, urlparse

import pytest
from Cryptodome.Cipher import AES

from opengrab.models import Link
from opengrab.plugins.base import PluginError
from opengrab.plugins.builtin import _megacore as mc
from opengrab.plugins.builtin import mega, transferit
from opengrab.plugins.builtin.mega import MegaPlugin, parse_link
from opengrab.plugins.builtin.transferit import TransferItPlugin
from opengrab.plugins.registry import PluginRegistry


@pytest.fixture(autouse=True)
def _clear_caches():
    mega._LISTINGS._d.clear()
    transferit._LISTINGS._d.clear()
    yield


# ---------------------------------------------------------------------------
# fake API
# ---------------------------------------------------------------------------
class Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._p = payload

    def json(self):
        return json.loads(json.dumps(self._p))


class FakeClient:
    """Answers Mega API commands from a handler(cmd, query) -> result."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def post(self, url, json_body=None, **kw):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        cmd = json_body[0]
        self.calls.append((cmd, q))
        res = self.handler(cmd, q)
        if isinstance(res, Resp):
            return res
        return Resp([res])


class Ctx:
    def __init__(self, client):
        self.client = client


def plugin(cls):
    p = cls()
    p.api_factory = lambda client: mc.MegaApi(client, sleep=lambda s: None)
    return p


def b64(a32):
    return mc.b64url_encode(mc.a32_to_bytes(a32))


def rand_a32(n):
    return mc.bytes_to_a32(os.urandom(4 * n))


# ---------------------------------------------------------------------------
# link parsing
# ---------------------------------------------------------------------------
FK = "c7YqnyWjltPyRC4G0gSmLRG2-UMexZDMdhdOtqp3IYA"   # 43 chars: file key
DK = "q8tH_ecjDOVqFXDbbzHntX"                        # 22 chars: folder key


@pytest.mark.parametrize("url,kind,handle,key,sub,sub_kind", [
    (f"https://mega.nz/file/V0olASyS#{FK}", "file", "V0olASyS", FK, "", ""),
    (f"https://www.mega.nz/file/V0olASyS#{FK}", "file", "V0olASyS", FK, "", ""),
    (f"https://mega.co.nz/file/V0olASyS#{FK}", "file", "V0olASyS", FK, "", ""),
    (f"https://mega.app/file/V0olASyS#{FK}", "file", "V0olASyS", FK, "", ""),
    (f"https://mega.nz/#!V0olASyS!{FK}", "file", "V0olASyS", FK, "", ""),
    (f"https://mega.co.nz/#!V0olASyS!{FK}", "file", "V0olASyS", FK, "", ""),
    (f"http://www.mega.co.nz/#!V0olASyS!{FK}", "file", "V0olASyS", FK, "", ""),
    (f"https://mega.nz/embed/V0olASyS#{FK}", "file", "V0olASyS", FK, "", ""),
    (f"https://mega.nz/embed#!V0olASyS!{FK}", "file", "V0olASyS", FK, "", ""),
    ("https://mega.nz/file/V0olASyS", "file", "V0olASyS", "", "", ""),
    (f"https://mega.nz/folder/AbCd-_12#{DK}", "folder", "AbCd-_12", DK, "", ""),
    (f"https://mega.co.nz/folder/AbCd-_12#{DK}", "folder", "AbCd-_12", DK, "", ""),
    (f"https://mega.nz/folder/AbCd-_12#{DK}/file/Nn0de_xY", "folder", "AbCd-_12", DK, "Nn0de_xY", "file"),
    (f"https://mega.nz/folder/AbCd-_12#{DK}/folder/Sub0fold", "folder", "AbCd-_12", DK, "Sub0fold", "folder"),
    (f"https://mega.nz/#F!AbCd-_12!{DK}", "folder", "AbCd-_12", DK, "", ""),
    (f"https://mega.co.nz/#F!AbCd-_12!{DK}", "folder", "AbCd-_12", DK, "", ""),
    (f"https://mega.nz/#F!AbCd-_12!{DK}!Nn0de_xY", "folder", "AbCd-_12", DK, "Nn0de_xY", ""),
    (f"https://mega.nz/#F!AbCd-_12!{DK}?Nn0de_xY", "folder", "AbCd-_12", DK, "Nn0de_xY", ""),
])
def test_parse_link_forms(url, kind, handle, key, sub, sub_kind):
    got = parse_link(url)
    assert got == {"kind": kind, "handle": handle, "key": key, "sub": sub,
                   "sub_kind": sub_kind}
    assert MegaPlugin().supports(url)


@pytest.mark.parametrize("url", [
    "https://mega.nz/",
    "https://mega.nz/pro",
    "https://mega.nz/file/short#key",
    "https://notmega.nz/file/V0olASyS#" + FK,
    "https://example.com/#!V0olASyS!" + FK,
    "https://transfer.it/t/AbCdEfGhIjKl",
])
def test_parse_link_rejects(url):
    assert parse_link(url) is None
    assert not MegaPlugin().supports(url)


def test_child_url_round_trips():
    u = mega.child_url("AbCd-_12", DK, "Nn0de_xY")
    assert u == f"https://mega.nz/folder/AbCd-_12#{DK}/file/Nn0de_xY"
    assert parse_link(u)["sub"] == "Nn0de_xY"


def test_transferit_link_forms():
    p = TransferItPlugin()
    assert transferit.parse_link("https://transfer.it/t/AbCdEfGhIjKl") == \
        {"xh": "AbCdEfGhIjKl", "node": ""}
    assert transferit.parse_link("https://transfer.it/t/AbCdEfGhIjKl/") == \
        {"xh": "AbCdEfGhIjKl", "node": ""}
    child = transferit.child_url("AbCdEfGhIjKl", "Nn0de_xY")
    assert transferit.parse_link(child) == {"xh": "AbCdEfGhIjKl", "node": "Nn0de_xY"}
    assert p.supports(child)
    for bad in ("https://transfer.it/", "https://transfer.it/t/short",
                "https://transfer.it.example.com/t/AbCdEfGhIjKl",
                f"https://mega.nz/file/V0olASyS#{FK}"):
        assert not p.supports(bad)


def test_registry_discovers_both():
    reg = PluginRegistry()
    reg.scan(force=True)
    assert "mega" in reg.plugins and "transferit" in reg.plugins
    assert not any(k.startswith("builtin:_megacore") for k in reg.errors)
    ids = [p.id for p in reg.for_url(f"https://mega.nz/file/V0olASyS#{FK}")]
    assert ids and ids[0] == "mega"
    ids = [p.id for p in reg.for_url("https://transfer.it/t/AbCdEfGhIjKl")]
    assert ids and ids[0] == "transferit"


# ---------------------------------------------------------------------------
# crypto
# ---------------------------------------------------------------------------
def test_real_attribute_vector():
    # Public file (MockbaMod MidiLoop zip): "at" as returned by the API and
    # the key from its public link.
    at = ("L3WF1I9ZL8Ld0chsqgDcQWiA_aTR0rBvY3NcpIOQhhRJlbiNqf12WLTmbm7GfCh_"
          "2FX5Rv_O3tiVXtixb_etvqWXgqyiMNLvHuwz7ZHy8yA")
    key = mc.unpack_file_key(mc.b64_to_a32(FK))[0]
    assert mc.decrypt_attr(at, key)["n"] == "MidiLoop-Latest-v4.056.zip"


def test_attr_round_trip_and_wrong_key():
    k = os.urandom(16)
    at = mc.encrypt_attr({"n": "Ünïcode file.bin", "c": "x"}, k)
    assert mc.decrypt_attr(at, k)["n"] == "Ünïcode file.bin"
    with pytest.raises(PluginError, match="key in the link is wrong"):
        mc.decrypt_attr(at, os.urandom(16))


def test_a32_and_key_unpack():
    k = [1, 2, 3, 4, 5, 6, 7, 8]
    assert mc.b64_to_a32(b64(k)) == k
    aes, nonce, mm = mc.unpack_file_key(k)
    assert mc.bytes_to_a32(aes) == [1 ^ 5, 2 ^ 6, 3 ^ 7, 4 ^ 8]
    assert mc.bytes_to_a32(nonce) == [5, 6] and mm == [7, 8]
    with pytest.raises(PluginError):
        mc.unpack_file_key([1, 2, 3, 4])


def test_node_key_wrap_round_trip():
    master = rand_a32(4)
    k = rand_a32(8)
    assert mc.decrypt_key(mc.encrypt_key(k, master), master) == k


def test_chunk_bounds():
    KB = 1024
    b = mc.chunk_bounds(5 * 1024 * KB)
    sizes = [n for _, n in b]
    assert sizes[:8] == [128 * KB * i for i in range(1, 9)]
    assert sum(sizes) == 5 * 1024 * KB
    assert all(n == 1024 * KB for n in sizes[8:-1])
    assert mc.chunk_bounds(10) == [(0, 10)]
    assert mc.chunk_bounds(0) == []
    offs = [o for o, _ in b]
    assert offs == sorted(offs) and offs[0] == 0


def _naive_meta_mac(plain, key, nonce):
    """Independent MAC: explicit per-block CBC with raw ECB."""
    ecb = AES.new(key, AES.MODE_ECB)
    xor = lambda a, b: bytes(x ^ y for x, y in zip(a, b))
    file_mac = b"\0" * 16
    for off, n in mc.chunk_bounds(len(plain)):
        chunk = plain[off:off + n]
        chunk += b"\0" * (-len(chunk) % 16)
        cmac = nonce + nonce
        for i in range(0, len(chunk), 16):
            cmac = ecb.encrypt(xor(cmac, chunk[i:i + 16]))
        file_mac = ecb.encrypt(xor(file_mac, cmac))
    m = mc.bytes_to_a32(file_mac)
    return [m[0] ^ m[1], m[2] ^ m[3]]


def test_mac_matches_independent_implementation():
    plain = os.urandom(300 * 1024 + 7)
    key, nonce = os.urandom(16), os.urandom(8)
    chunks = [plain[o:o + n] for o, n in mc.chunk_bounds(len(plain))]
    assert mc.meta_mac_stream(chunks, key, nonce) == _naive_meta_mac(plain, key, nonce)


def test_ctr_matches_counter_layout():
    # CTR block i uses nonce || big-endian 64-bit i.
    plain = os.urandom(64)
    fk = rand_a32(8)
    key, nonce, _ = mc.unpack_file_key(fk)
    ecb = AES.new(key, AES.MODE_ECB)
    ks = b"".join(ecb.encrypt(nonce + i.to_bytes(8, "big")) for i in range(4))
    assert mc.encrypt_bytes(plain, fk) == bytes(a ^ b for a, b in zip(plain, ks))


# ---------------------------------------------------------------------------
# fixtures: a single file
# ---------------------------------------------------------------------------
def make_file(size, name="fixture.bin"):
    plain = os.urandom(size)
    fk = mc.make_file_key(plain)
    enc = mc.encrypt_bytes(plain, fk)
    at = mc.encrypt_attr({"n": name}, mc.unpack_file_key(fk)[0])
    return plain, fk, enc, at


def file_api(size, at, *, url="https://gfs1.userstorage.mega.co.nz/dl/abc"):
    def handler(cmd, q):
        assert cmd["a"] == "g" and cmd["p"] == "Fh4ndle0"
        res = {"s": size, "at": at}
        if cmd.get("g"):
            assert cmd.get("ssl") == 1
            res["g"] = url
        return res
    return FakeClient(handler)


def test_file_grab_resolve_and_finalize(tmp_path):
    plain, fk, enc, at = make_file(2 * 1024 * 1024 + 333, "My Movie.mkv")
    client = file_api(len(plain), at)
    p = plugin(MegaPlugin)
    url = f"https://mega.nz/file/Fh4ndle0#{b64(fk)}"

    g = p.grab(url, Ctx(client))
    assert g.title == "My Movie.mkv" and g.size == len(plain)
    assert g.direct_url is None  # resolve() must still run to store the key
    assert "g" not in client.calls[-1][0]

    r = p.resolve(url, Ctx(client))
    assert r.url.startswith("https://gfs1.")
    assert r.filename == "My Movie.mkv" and r.size == len(plain)
    assert mc.b64_to_a32(r.extra["mega"]["key"]) == fk

    path = tmp_path / r.filename
    path.write_bytes(enc)
    link = Link(url=url)
    link.extra.update(r.extra)          # what engine._resolve does
    link.extra = json.loads(json.dumps(link.extra))  # survives persistence
    out = p.finalize(path, link, None)
    assert out == path
    assert path.read_bytes() == plain
    assert not (tmp_path / (r.filename + ".megadec")).exists()


def test_finalize_detects_corruption(tmp_path):
    plain, fk, enc, _ = make_file(200 * 1024)
    bad = bytearray(enc)
    bad[150 * 1024] ^= 1
    path = tmp_path / "f.bin"
    path.write_bytes(bytes(bad))
    link = Link(url="https://mega.nz/file/Fh4ndle0#x")
    link.extra["mega"] = {"key": b64(fk), "size": len(plain), "verify_mac": True}
    with pytest.raises(PluginError, match="MAC mismatch"):
        MegaPlugin().finalize(path, link, None)
    assert not path.exists()  # removed so the retry downloads it again
    assert list(tmp_path.iterdir()) == []


def test_finalize_size_mismatch_and_noop(tmp_path):
    plain, fk, enc, _ = make_file(1000)
    path = tmp_path / "f.bin"
    path.write_bytes(enc[:500])
    link = Link(url="https://mega.nz/file/Fh4ndle0#x")
    link.extra["mega"] = {"key": b64(fk), "size": len(plain)}
    with pytest.raises(PluginError, match="size mismatch"):
        MegaPlugin().finalize(path, link, None)
    other = Link(url="https://mega.nz/file/Fh4ndle0#x")
    assert MegaPlugin().finalize(path, other, None) == path  # nothing stored


def test_finalize_small_and_empty(tmp_path):
    for size in (0, 1, 15, 16, 17):
        plain, fk, enc, _ = make_file(size)
        path = tmp_path / f"f{size}.bin"
        path.write_bytes(enc)
        link = Link(url="https://mega.nz/file/Fh4ndle0#x")
        link.extra["mega"] = {"key": b64(fk), "size": size}
        TransferItPlugin().finalize(path, link, None)
        assert path.read_bytes() == plain


def test_missing_or_bad_key():
    p = plugin(MegaPlugin)
    with pytest.raises(PluginError, match="no decryption key"):
        p.resolve("https://mega.nz/file/Fh4ndle0", Ctx(FakeClient(lambda c, q: {})))
    with pytest.raises(PluginError, match="incomplete"):
        p.resolve(f"https://mega.nz/file/Fh4ndle0#{DK}", Ctx(FakeClient(lambda c, q: {})))


# ---------------------------------------------------------------------------
# API errors
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code,needle", [
    (-9, "not found"), (-11, "access denied"), (-16, "taken down"),
    (-18, "temporarily unavailable"), (-14, "key in the link is wrong"),
    (-8, "expired"),
])
def test_api_error_messages(code, needle):
    p = plugin(MegaPlugin)
    url = f"https://mega.nz/file/Fh4ndle0#{FK}"
    with pytest.raises(PluginError, match=needle) as ei:
        p.resolve(url, Ctx(FakeClient(lambda c, q: code)))
    assert f"code {code}" in str(ei.value)
    # whole-request failures arrive as a bare integer, not a list
    client = FakeClient(lambda c, q: Resp(code))
    with pytest.raises(PluginError, match=needle):
        p.resolve(url, Ctx(client))


def test_eagain_retries_then_succeeds():
    _plain, fk, _enc, at = make_file(100)
    answers = [-3, -3]
    def handler(cmd, q):
        return answers.pop(0) if answers else {"s": 100, "at": at, "g": "https://gfs/x"}
    client = FakeClient(handler)
    r = plugin(MegaPlugin).resolve(f"https://mega.nz/file/Fh4ndle0#{b64(fk)}", Ctx(client))
    assert r.url == "https://gfs/x" and len(client.calls) == 3


def test_eagain_gives_up():
    with pytest.raises(PluginError, match="busy"):
        plugin(MegaPlugin).resolve(f"https://mega.nz/file/Fh4ndle0#{FK}",
                                   Ctx(FakeClient(lambda c, q: -3)))


def test_http_errors():
    with pytest.raises(PluginError, match="busy"):  # 5xx is retried, then busy
        plugin(MegaPlugin).resolve(f"https://mega.nz/file/Fh4ndle0#{FK}",
                                   Ctx(FakeClient(lambda c, q: Resp(None, 500))))
    with pytest.raises(PluginError, match="HTTP 403"):
        plugin(MegaPlugin).resolve(f"https://mega.nz/file/Fh4ndle0#{FK}",
                                   Ctx(FakeClient(lambda c, q: Resp(None, 403))))


def test_transfer_quota_reports_wait():
    _p, fk, _e, at = make_file(10)
    url = f"https://mega.nz/file/Fh4ndle0#{b64(fk)}"
    # quota as an error object with the time left
    c1 = FakeClient(lambda c, q: {"e": -17, "tl": 1500})
    with pytest.raises(PluginError, match=r"quota exceeded.*25 min") as ei:
        plugin(MegaPlugin).resolve(url, Ctx(c1))
    assert ei.value.wait_s == 1500
    # metadata but no URL, only "tl"
    c2 = FakeClient(lambda c, q: {"s": 10, "at": at, "tl": 7300})
    with pytest.raises(PluginError, match=r"2 h 2 min"):
        plugin(MegaPlugin).resolve(url, Ctx(c2))
    # bare over-quota code without a time
    c3 = FakeClient(lambda c, q: -17)
    with pytest.raises(PluginError, match="quota exceeded; wait for the quota"):
        plugin(MegaPlugin).resolve(url, Ctx(c3))


def test_cloudraid_rejected_clearly():
    _p, fk, _e, at = make_file(10)
    c = FakeClient(lambda cmd, q: {"s": 10, "at": at, "g": ["https://a", "https://b"]})
    with pytest.raises(PluginError, match="CloudRAID"):
        plugin(MegaPlugin).resolve(f"https://mega.nz/file/Fh4ndle0#{b64(fk)}", Ctx(c))


# ---------------------------------------------------------------------------
# folders
# ---------------------------------------------------------------------------
def build_folder():
    """Root folder 'Album' with a.txt, B/b.bin, B/C/c.dat; key-wrapped nodes."""
    fkey = rand_a32(4)
    files = {}
    nodes = []

    def add_folder(h, parent, name, key=None):
        k = key or rand_a32(4)
        nodes.append({"h": h, "p": parent, "t": 1,
                      "a": mc.encrypt_attr({"n": name}, mc.a32_to_bytes(k)),
                      "k": f"R00tnode:{b64(mc.encrypt_key(k, fkey))}"})

    def add_file(h, parent, name, size):
        plain, fk, enc, at = make_file(size, name)
        files[h] = (plain, fk, enc, at)
        nodes.append({"h": h, "p": parent, "t": 0, "s": size, "a": at,
                      "k": f"R00tnode:{b64(mc.encrypt_key(fk, fkey))}"})

    add_folder("R00tnode", "Ownerxyz", "Album", key=fkey)
    add_file("Afile001", "R00tnode", "a.txt", 50)
    add_folder("Bfold001", "R00tnode", "B")
    add_file("Bfile001", "Bfold001", "b.bin", 70000)
    add_folder("Cfold001", "Bfold001", "C")
    add_file("Cfile001", "Cfold001", "c.dat", 3)
    # undecryptable node (foreign key) is skipped, as the web client does
    nodes.append({"h": "Junk0001", "p": "R00tnode", "t": 0, "s": 1,
                  "a": mc.encrypt_attr({"n": "x"}, os.urandom(16)),
                  "k": f"R00tnode:{b64(rand_a32(8))}"})
    return fkey, nodes, files


def folder_client(nodes, files, fh="Fold3r00"):
    def handler(cmd, q):
        assert q.get("n") == fh, "folder requests carry the folder handle"
        if cmd["a"] == "f":
            assert cmd["c"] == 1 and cmd["r"] == 1
            return {"f": nodes, "ok": [], "s": [], "u": []}
        if cmd["a"] == "g":
            h = cmd["n"]
            _p, _fk, enc, at = files[h]
            res = {"s": len(enc), "at": at}
            if cmd.get("g"):
                res["g"] = f"https://gfs/{h}"
            return res
        return -2
    return FakeClient(handler)


def test_folder_lists_children_and_child_downloads(tmp_path):
    fkey, nodes, files = build_folder()
    client = folder_client(nodes, files)
    p = plugin(MegaPlugin)
    url = f"https://mega.nz/folder/Fold3r00#{b64(fkey)}"

    g = p.grab(url, Ctx(client))
    assert g.title == "Album" and g.direct_url is None
    r = p.resolve(url, Ctx(client))
    assert r.url == ""
    titles = [t for _u, t in r.children]
    assert titles == ["a.txt", "B/b.bin", "B/C/c.dat"]
    assert r.size == 50 + 70000 + 3
    for child, _t in r.children:
        assert p.supports(child)
        assert child.startswith(f"https://mega.nz/folder/Fold3r00#{b64(fkey)}/file/")
    # listing is cached: grab + resolve listed once
    assert sum(1 for c, _ in client.calls if c["a"] == "f") == 1

    # a child resolves to a decryptable file
    child = dict((t, u) for u, t in r.children)["B/b.bin"]
    rc = p.resolve(child, Ctx(client))
    assert rc.url == "https://gfs/Bfile001" and rc.filename == "b.bin"
    plain, _fk, enc, _at = files["Bfile001"]
    path = tmp_path / rc.filename
    path.write_bytes(enc)
    link = Link(url=child)
    link.extra.update(rc.extra)
    assert p.finalize(path, link, None).read_bytes() == plain


def test_subfolder_and_legacy_folder_links():
    fkey, nodes, files = build_folder()
    client = folder_client(nodes, files)
    p = plugin(MegaPlugin)
    r = p.resolve(f"https://mega.nz/folder/Fold3r00#{b64(fkey)}/folder/Bfold001",
                  Ctx(client))
    assert [t for _u, t in r.children] == ["b.bin", "C/c.dat"]
    # legacy #F! form with a file node
    r2 = p.resolve(f"https://mega.co.nz/#F!Fold3r00!{b64(fkey)}!Cfile001", Ctx(client))
    assert r2.filename == "c.dat" and r2.url == "https://gfs/Cfile001"
    with pytest.raises(PluginError, match="not in this Mega folder"):
        p.resolve(f"https://mega.nz/folder/Fold3r00#{b64(fkey)}/file/Nope0000", Ctx(client))


def test_folder_wrong_key():
    _fkey, nodes, files = build_folder()
    with pytest.raises(PluginError, match="key"):
        plugin(MegaPlugin).resolve(
            f"https://mega.nz/folder/Fold3r00#{b64(rand_a32(4))}",
            Ctx(folder_client(nodes, files)))


# ---------------------------------------------------------------------------
# transfer.it
# ---------------------------------------------------------------------------
def build_transfer(n_files=2, pw=0):
    root_k = rand_a32(4)
    nodes = [{"h": "XR00t000", "p": "", "t": 1,
              "a": mc.encrypt_attr({"n": "Transfer.it 2026"}, mc.a32_to_bytes(root_k)),
              "k": b64(root_k)}]
    files = {}
    for i in range(n_files):
        h = f"Xfile00{i}"
        plain, fk, enc, at = make_file(1000 + i, f"part{i}.zip")
        files[h] = (plain, fk, enc, at)
        nodes.append({"h": h, "p": "XR00t000", "t": 0, "s": len(plain),
                      "a": at, "k": b64(fk)})

    def handler(cmd, q):
        if cmd["a"] == "xi":
            assert cmd["xh"] == "TrAnSfEr1234"
            return {"z": "Zip00000", "pw": pw, "size": [0, n_files, 1, 0, 0]}
        assert q.get("x") == "TrAnSfEr1234", "transfer requests carry x=<xh>"
        if cmd["a"] == "f":
            return {"f": nodes}
        if cmd["a"] == "g":
            _p, _fk, enc, at = files[cmd["n"]]
            res = {"s": len(enc), "at": at}
            if cmd.get("g"):
                assert cmd.get("pt") == 1
                res["g"] = f"https://gfs/{cmd['n']}"
            return res
        return -2
    return FakeClient(handler), files


def test_transferit_multi_file(tmp_path):
    client, files = build_transfer(2)
    p = plugin(TransferItPlugin)
    url = "https://transfer.it/t/TrAnSfEr1234"
    g = p.grab(url, Ctx(client))
    assert g.title == "Transfer.it 2026" and g.direct_url is None
    r = p.resolve(url, Ctx(client))
    assert r.url == "" and [t for _u, t in r.children] == ["part0.zip", "part1.zip"]
    child = r.children[1][0]
    assert child == "https://transfer.it/t/TrAnSfEr1234#file=Xfile001"
    rc = p.resolve(child, Ctx(client))
    assert rc.url == "https://gfs/Xfile001" and rc.filename == "part1.zip"
    plain, _fk, enc, _at = files["Xfile001"]
    path = tmp_path / "part1.zip"
    path.write_bytes(enc)
    link = Link(url=child)
    link.extra.update(rc.extra)
    assert p.finalize(path, link, None).read_bytes() == plain


def test_transferit_single_file_is_direct():
    client, _files = build_transfer(1)
    r = plugin(TransferItPlugin).resolve("https://transfer.it/t/TrAnSfEr1234", Ctx(client))
    assert r.url == "https://gfs/Xfile000" and r.filename == "part0.zip"
    assert "mega" in r.extra and not r.children


def test_transferit_errors():
    client, _ = build_transfer(1, pw=1)
    with pytest.raises(PluginError, match="password protected"):
        plugin(TransferItPlugin).resolve("https://transfer.it/t/TrAnSfEr1234", Ctx(client))
    with pytest.raises(PluginError, match="transfer not found"):
        plugin(TransferItPlugin).resolve("https://transfer.it/t/TrAnSfEr1234",
                                         Ctx(FakeClient(lambda c, q: -9)))
    with pytest.raises(PluginError, match="expired"):
        plugin(TransferItPlugin).resolve("https://transfer.it/t/TrAnSfEr1234",
                                         Ctx(FakeClient(lambda c, q: -8)))
