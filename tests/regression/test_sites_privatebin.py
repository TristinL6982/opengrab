"""PrivateBin pastes and 1337x topic pages. The paste fixture is encrypted
here exactly as PrivateBin's page does it (format v2, AES-256-GCM)."""
import base64
import json
import os
import zlib
from types import SimpleNamespace

import pytest
from Cryptodome.Cipher import AES
from Cryptodome.Hash import SHA256
from Cryptodome.Protocol.KDF import PBKDF2

from opengrab.plugins.base import LoginRequired, PluginError
from opengrab.plugins.builtin import privatebin as pb
from opengrab.plugins.builtin import trackers


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = pb.B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def make_paste(text: str, password: str = "", body: dict | None = None):
    key, salt, iv = os.urandom(32), os.urandom(8), os.urandom(16)
    adata = [[base64.b64encode(iv).decode(), base64.b64encode(salt).decode(), 100000, 256, 128,
              "aes", "gcm", "zlib"], "plaintext", 0, 0]
    derived = PBKDF2(key + password.encode(), salt, dkLen=32, count=100000, hmac_hash_module=SHA256)
    comp = zlib.compressobj(wbits=-15)
    plain = comp.compress(json.dumps(body or {"paste": text}).encode()) + comp.flush()
    c = AES.new(derived, AES.MODE_GCM, nonce=iv, mac_len=16)
    c.update(json.dumps(adata, separators=(",", ":")).encode())
    ct, tag = c.encrypt_and_digest(plain)
    paste = {"status": 0, "id": "eeb22a8b461f9023", "v": 2, "adata": adata,
             "ct": base64.b64encode(ct + tag).decode(), "meta": {}}
    return paste, b58encode(key)


class Ctx:
    def __init__(self, answer, password=""):
        self.answer, self.urls, self.password = answer, [], password
        self.client = SimpleNamespace(get=self._get)

    def _get(self, url, headers=None, **kw):
        self.urls.append((url, (headers or {}).get("x-requested-with")))
        return SimpleNamespace(status_code=200, json=lambda: self.answer)

    def account_for(self, site=None, url=None):
        return SimpleNamespace(password=self.password) if self.password else None


def test_privatebin_links_are_recognised_on_any_host():
    paste, key = make_paste("x")
    assert pb.PrivateBinPlugin().supports(f"https://paste.example.org/?eeb22a8b461f9023#{key}")
    assert pb.PrivateBinPlugin().supports(f"https://bin.example.net/?pasteid=eeb22a8b461f9023#-{key}")
    assert not pb.PrivateBinPlugin().supports("https://paste.example.org/?eeb22a8b461f9023")  # no key
    assert not pb.PrivateBinPlugin().supports("https://example.org/page?x=1#top")


def test_paste_is_decrypted_and_every_link_becomes_a_download():
    text = ("Part 1: https://datanodes.to/abc123/file.part01.rar\n"
            "Part 2: https://fuckingfast.co/xyz#file.part02.rar\n"
            "mirror magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=x\n")
    paste, key = make_paste(text)
    ctx = Ctx(paste)
    r = pb.PrivateBinPlugin().resolve(f"https://paste.example.org/?eeb22a8b461f9023#{key}", ctx)
    assert [c[0] for c in r.children] == [
        "https://datanodes.to/abc123/file.part01.rar", "https://fuckingfast.co/xyz#file.part02.rar",
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=x"]
    assert ctx.urls == [("https://paste.example.org/?pasteid=eeb22a8b461f9023", "JSONHttpRequest")]


def test_links_in_an_attached_text_file_count_too():
    att = "data:text/plain;base64," + base64.b64encode(b"https://example.org/a.part1.rar\n").decode()
    pic = "data:image/png;base64," + base64.b64encode(b"https://example.org/not-a-link").decode()
    paste, key = make_paste("", body={"paste": "", "attachment": [att, pic], "formatter": "plaintext"})
    r = pb.PrivateBinPlugin().resolve(f"https://paste.example.org/?eeb22a8b461f9023#{key}", Ctx(paste))
    assert [c[0] for c in r.children] == ["https://example.org/a.part1.rar"]


def test_password_pastes_use_the_saved_password_and_say_when_it_is_missing():
    paste, key = make_paste("https://example.org/f.zip", password="hunter2")
    url = f"https://paste.example.org/?eeb22a8b461f9023#{key}"
    with pytest.raises(LoginRequired) as e:
        pb.PrivateBinPlugin().resolve(url, Ctx(paste))
    assert "needs a password" in e.value.hint
    with pytest.raises(LoginRequired) as e:
        pb.PrivateBinPlugin().resolve(url, Ctx(paste, password="nope"))
    assert "wrong" in e.value.hint
    r = pb.PrivateBinPlugin().resolve(url, Ctx(paste, password="hunter2"))
    assert r.children[0][0] == "https://example.org/f.zip"


def test_expired_paste_and_non_privatebin_site():
    paste, key = make_paste("x")
    url = f"https://paste.example.org/?eeb22a8b461f9023#{key}"
    with pytest.raises(PluginError, match="expired"):
        pb.PrivateBinPlugin().resolve(url, Ctx({"status": 1, "message": "Paste not found, does not exist or has expired."}))

    class NotJson(Ctx):
        def _get(self, url, headers=None, **kw):
            return SimpleNamespace(status_code=200, json=lambda: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(PluginError, match="did not answer like PrivateBin"):
        pb.PrivateBinPlugin().resolve(url, NotJson({}))


def test_1337x_topic_page_becomes_its_magnet():
    html = ('<html><head><title>Download Some Linux ISO torrent | 1337x</title></head><body>'
            '<h1>Some Linux ISO 24.04</h1><ul><li><a href="magnet:?xt=urn:btih:ABCDEF0123456789ABCDEF0123456789ABCDEF01'
            '&amp;dn=Some+Linux+ISO&amp;tr=udp%3A%2F%2Ftracker.example%3A1337">Magnet Download</a></li></ul></body></html>')
    p = trackers.X1337Plugin()
    assert p.supports("https://1337x.to/torrent/6750160/Some-Linux-ISO/")
    assert p.supports("https://x1337x.ws/torrent/1/a/")
    assert not p.supports("https://1337x.to/search/linux/1/")
    ctx = SimpleNamespace(settings=SimpleNamespace(),
                          client=SimpleNamespace(get=lambda url, **kw: SimpleNamespace(
                              status_code=200, url=url, text=html, content=html.encode(), headers={})))
    r = p.resolve("https://1337x.to/torrent/6750160/Some-Linux-ISO/", ctx)
    assert r.children[0][0].startswith("magnet:?xt=urn:btih:ABCDEF") and "&dn=Some+Linux+ISO" in r.children[0][0]
    assert r.children[0][1] == "Some Linux ISO 24.04"
