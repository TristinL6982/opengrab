"""The Add box only takes links: stray text must not become a download."""
from tests.helpers.apiserver import req, running_server


def test_text_that_is_not_a_link_is_refused_and_bare_domains_get_https():
    with running_server() as srv:
        st, out = req(srv, "POST", "/api/urls", body={"auto_download": False, "urls": [
            "oD_Some_Game_--_example-site.site_--_.part01.rar",
            "just some words",
            "example.com/files/a.zip",
            "https://example.com/b.zip#name",
            "magnet:?xt=urn:btih:" + "a" * 40,
        ]})
        assert st == 200
        assert "not a link" in out[0]["error"] and "not a link" in out[1]["error"]
        assert out[2]["url"] == "https://example.com/files/a.zip"
        assert out[3]["url"] == "https://example.com/b.zip#name"
        assert out[4]["url"].startswith("magnet:?")
        urls = [link["url"] for link in req(srv, "GET", "/api/links")[1]]
        assert not any("Some_Game" in u for u in urls) and len(urls) == 3
