"""A request answered before its body was read must not corrupt the next
request on the same keep-alive connection (found by the UI click-through,
2026-10-31: "501 Unsupported method ('{}GET')")."""
import http.client
import json

from tests.helpers.apiserver import MASTER_AUTH, running_server


def _conn(srv):
    return http.client.HTTPConnection("127.0.0.1", srv.port_used, timeout=10)


def test_refused_upload_does_not_poison_the_next_request():
    with running_server() as srv:
        c = _conn(srv)
        body = b"hello from a refused upload"
        for _ in range(2):  # second upload to the same name is refused before its body is read
            c.request("PUT", "/api/files/upload?path=same.txt", body=body,
                      headers={**MASTER_AUTH, "Content-Type": "application/octet-stream"})
            r = c.getresponse()
            r.read()
        assert r.status == 400
        if r.getheader("Connection", "").lower() == "close" or r.will_close:
            c = _conn(srv)  # the server may close instead of draining; the client reconnects
        c.request("GET", "/api/settings", headers=MASTER_AUTH)
        r = c.getresponse()
        assert r.status == 200 and "download_dir" in json.loads(r.read())


def test_refused_json_post_does_not_poison_the_next_request():
    with running_server() as srv:
        c = _conn(srv)
        # an endpoint that refuses before reading the body: an unknown device token
        c.request("POST", "/api/avscan/test", body=json.dumps({"x": 1}),
                  headers={"Authorization": "Bearer wrong-token", "Content-Type": "application/json"})
        r = c.getresponse()
        r.read()
        assert r.status in (401, 403)
        c.request("GET", "/api/ping", headers=MASTER_AUTH)
        r = c.getresponse()
        assert r.status == 200, r.read()
