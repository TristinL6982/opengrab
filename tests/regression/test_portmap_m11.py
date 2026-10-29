from opengrab.portmap import PortMapConfig, PortMapper, config_from_dict, valid_port


class FakeClient:
    def __init__(self, external=None, fail_on=None):
        self.external = external
        self.fail_on = fail_on
        self.added = []
        self.removed = []

    def add_mapping(self, port, protocol, description):
        if self.fail_on == protocol:
            raise OSError("router said no")
        self.added.append((port, protocol, description))
        return self.external if self.external is not None else port

    def remove_mapping(self, port, protocol):
        self.removed.append((port, protocol))


def test_valid_port():
    assert valid_port(6881)
    assert not valid_port(0)
    assert not valid_port(70000)
    assert not valid_port("6881")
    assert not valid_port(True)


def test_config_from_dict_cleans_values():
    cfg = config_from_dict({"port": 51413, "protocols": ["TCP", "bogus", "tcp"], "methods": ["natpmp"], "manual": True})
    assert cfg.port == 51413
    assert cfg.protocols == ("tcp",)
    assert cfg.methods == ("natpmp",)
    assert cfg.manual is True
    bad = config_from_dict({"port": 99999, "enabled": "yes"})
    assert bad.port == 6881
    assert bad.enabled is True
    assert config_from_dict(None) == PortMapConfig()


def test_upnp_first():
    upnp = FakeClient()
    natpmp = FakeClient()
    mapper = PortMapper(PortMapConfig(port=7000), {"upnp": upnp, "natpmp": natpmp})
    res = mapper.map()
    assert res.ok
    assert res.method == "upnp"
    assert res.external_port == 7000
    assert res.protocols == ("tcp", "udp")
    assert natpmp.added == []


def test_falls_back_to_natpmp_and_rolls_back():
    upnp = FakeClient(fail_on="udp")
    natpmp = FakeClient(external=40000)
    mapper = PortMapper(PortMapConfig(port=7000), {"upnp": upnp, "natpmp": natpmp})
    res = mapper.map()
    assert res.ok
    assert res.method == "natpmp"
    assert res.external_port == 40000
    assert upnp.removed == [(7000, "tcp")]
    assert res.errors == ["upnp: router said no"]


def test_all_fail_and_missing_clients():
    mapper = PortMapper(PortMapConfig(), {"upnp": FakeClient(fail_on="tcp")})
    res = mapper.map()
    assert not res.ok
    assert res.errors == ["upnp: router said no", "natpmp: unavailable"]
    assert mapper.active is None


def test_manual_and_disabled():
    manual = PortMapper(PortMapConfig(port=5555, manual=True), {"upnp": FakeClient()})
    res = manual.map()
    assert res.ok
    assert res.method == "manual"
    assert res.external_port == 5555
    off = PortMapper(PortMapConfig(enabled=False))
    assert off.map().errors == ["disabled"]


def test_release_removes_mappings():
    upnp = FakeClient()
    mapper = PortMapper(PortMapConfig(port=7000), {"upnp": upnp})
    mapper.map()
    assert mapper.release() is True
    assert upnp.removed == [(7000, "tcp"), (7000, "udp")]
    assert mapper.release() is False


def test_reachable_uses_probe():
    seen = []

    def probe(port):
        seen.append(port)
        return True

    mapper = PortMapper(PortMapConfig(port=7000), {"upnp": FakeClient(external=8000)}, probe=probe)
    assert mapper.reachable() is None
    mapper.map()
    assert mapper.reachable() is True
    assert seen == [8000]

    def broken(port):
        raise OSError("timeout")

    mapper.probe = broken
    assert mapper.reachable() is False