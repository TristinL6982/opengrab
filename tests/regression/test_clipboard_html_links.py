"""Hyperlinks copied from a page: names in the text flavour, hrefs in CF_HTML."""
from pathlib import Path

from opengrab.clipboard_html import links_from_cf_html
from tests.helpers.apiserver import req, running_server

STATIC = Path(__file__).resolve().parents[2] / "src" / "opengrab" / "ui" / "static"


def cf_html(fragment: str, source: str = "https://pages.example.com/list/index.html") -> bytes:
    """Build a CF_HTML payload with correct byte offsets (made-up content)."""
    body = ("<html><body><!--StartFragment-->" + fragment + "<!--EndFragment--></body></html>").encode()
    template = ("Version:0.9\r\nStartHTML:{:010d}\r\nEndHTML:{:010d}\r\n"
                "StartFragment:{:010d}\r\nEndFragment:{:010d}\r\nSourceURL:" + source + "\r\n")
    hl = len(template.format(0, 0, 0, 0).encode())
    start = hl + body.index(b"<!--StartFragment-->") + len(b"<!--StartFragment-->")
    end = hl + body.index(b"<!--EndFragment-->")
    return template.format(hl, hl + len(body), start, end).encode() + body


def test_extracts_hrefs_in_order_and_dedupes():
    data = cf_html(
        '<a href="https://files.example.com/a/Pack.part01.rar">Pack.part01.rar</a><br>'
        '<a href="https://files.example.com/a/Pack.part02.rar">Pack.part02.rar</a>'
        '<a href="https://files.example.com/a/Pack.part01.rar">again</a>'
        '<a href="magnet:?xt=urn:btih:abc">m</a>')
    assert links_from_cf_html(data) == [
        "https://files.example.com/a/Pack.part01.rar",
        "https://files.example.com/a/Pack.part02.rar",
        "magnet:?xt=urn:btih:abc"]


def test_relative_links_resolve_against_source_url_and_junk_is_dropped():
    data = cf_html(
        '<a href="/dl/1.zip">one</a><a href="two.zip">two</a>'
        '<a href="#top">top</a><a href="javascript:void(0)">js</a>'
        '<a href="mailto:a@example.com">mail</a><a href="ftp://x.example.com/f">ftp</a>'
        '<a>no href</a><a href="  ">blank</a>')
    assert links_from_cf_html(data) == [
        "https://pages.example.com/dl/1.zip", "https://pages.example.com/list/two.zip"]


def test_only_the_fragment_counts_and_entities_are_decoded():
    data = cf_html('<a href="https://files.example.com/q?a=1&amp;b=2">x</a>')
    outside = data.replace(b"<body>", b'<body><a href="https://outside.example.com/no">n</a>', 1)
    # offsets shift, so rebuild: put the extra link before the header offsets are computed
    html = ('<a href="https://outside.example.com/no">n</a>')
    body = ("<html><body>" + html + "<!--StartFragment--><a href=\"https://files.example.com/q?a=1&amp;b=2\">x</a>"
            "<!--EndFragment--></body></html>").encode()
    s = body.index(b"<!--StartFragment-->") + len(b"<!--StartFragment-->")
    e = body.index(b"<!--EndFragment-->")
    head = "Version:0.9\r\nStartHTML:0000000105\r\nEndHTML:0000009999\r\nStartFragment:%010d\r\nEndFragment:%010d\r\n"
    hl = len((head % (0, 0)).encode())
    payload = (head % (hl + s, hl + e)).encode() + body
    assert links_from_cf_html(payload) == ["https://files.example.com/q?a=1&b=2"]
    assert outside  # (unused helper output; keeps the first build exercised)


def test_header_without_offsets_or_plain_html_still_works_and_empty_is_empty():
    assert links_from_cf_html('<p><a href="https://files.example.com/x.zip">x</a></p>') == [
        "https://files.example.com/x.zip"]
    assert links_from_cf_html(b"") == [] and links_from_cf_html("") == []
    assert links_from_cf_html(b"Version:0.9\r\nStartFragment:99999\r\nEndFragment:1\r\n<a href='https://files.example.com/y'>y</a>") == [
        "https://files.example.com/y"]


def test_watcher_merges_html_links_with_plain_text_urls():
    with running_server({"clipboard_monitor_enabled": True}) as srv:
        names_only = "Pack.part01.rar\nPack.part02.rar"
        assert srv.capture_clipboard_text(names_only) == 0  # the owner's failing case
        links = ["https://files.example.com/a/Pack.part01.rar", "https://files.example.com/a/Pack.part02.rar"]
        assert srv.capture_clipboard_text(names_only, links) == 2
        text = "also https://files.example.com/a/Pack.part01.rar and https://files.example.com/b.zip"
        assert srv.capture_clipboard_text(text, links) == 1  # only b.zip is new
        urls = [i["url"] for i in req(srv, "GET", "/api/linkgrabber")[1]]
        assert sorted(urls) == sorted(links + ["https://files.example.com/b.zip"])


def test_watcher_state_treats_an_html_only_copy_as_new():
    src = (Path(__file__).resolve().parents[2] / "src" / "opengrab" / "app.py").read_text(encoding="utf-8")
    assert 'payload.get("seq", 0), current, tuple(html_links)' in src
    assert "capture_clipboard_text(current, html_links)" in src


def test_ui_pastes_real_urls_and_explains_zero_links():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'getData("text/html")' in js and "DOMParser" in js
    assert '$("#url-input").addEventListener("paste"' in js
    assert '$("#lg-paste").addEventListener("paste"' in js
    assert "No links found. If you copied link names from a web page" in js
    assert 'api("/api/linkgrabber", "POST", { urls, source: "paste" })' in js
    assert "'.tab[data-tab=\"linkgrabber\"]').click()" in js.replace('$(\'', "'") or \
        '$(\'.tab[data-tab="linkgrabber"]\').click();' in js


def test_every_url_of_a_copy_is_added():
    """Owner's live test: several links copied at once must all arrive."""
    from opengrab.clipboard_html import urls_from_text
    base = "https://files.example.com/"
    lines = "\r\n".join(f"{base}id{i}/Pack.part0{i}.rar" for i in range(1, 6))
    with running_server({"clipboard_monitor_enabled": True}) as srv:
        assert srv.capture_clipboard_text(lines) == 5  # separate lines
        glued = "".join(f"{base}g{i}/f{i}.zip" for i in range(3))
        assert srv.capture_clipboard_text(glued) == 3  # no separator between them
        links = [f"{base}h{i}/Pack.part0{i}.rar" for i in range(1, 5)]
        assert srv.capture_clipboard_text("Pack.part01.rar\nPack.part02.rar", links) == 4  # HTML hrefs
        data = cf_html("".join(f'<a href="{u}">{u.rsplit("/", 1)[1]}</a>' for u in links))
        assert links_from_cf_html(data) == links
        assert len(req(srv, "GET", "/api/linkgrabber")[1]) == 12
    assert urls_from_text("a https://x.example.com/1, then https://x.example.com/2.") == [
        "https://x.example.com/1", "https://x.example.com/2"]


def test_appearance_choices_save_at_once_and_a_partial_post_keeps_other_settings():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "async function saveAppearance()" in js
    assert js.count("saveAppearance") >= 5  # theme/density/labels, accent change + reset, columns
    with running_server({"clipboard_monitor_enabled": True}) as srv:
        before = req(srv, "GET", "/api/settings")[1]
        body = {"ui_theme": "classic-dark", "ui_density": "compact", "ui_accent": "#336699",
                "ui_toolbar_labels": False, "ui_hidden_columns": ["eta"]}
        assert req(srv, "POST", "/api/settings", body)[0] == 200
        after = req(srv, "GET", "/api/settings")[1]
        for k, v in body.items():
            assert after[k] == v
        assert after["clipboard_monitor_enabled"] is True
        assert after["download_dir"] == before["download_dir"]
