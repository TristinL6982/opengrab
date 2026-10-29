"""Port mapping for the torrent listen port (M11k).

Tries UPnP, then NAT-PMP, or uses a manual port, and offers a reachability test.
Pure module: the router clients and the reachability probe are injected, so nothing here
opens a socket or makes a network request by itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PROTOCOLS = ("tcp", "udp")
METHODS = ("upnp", "natpmp")


def valid_port(port) -> bool:
    return isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535


@dataclass
class PortMapConfig:
    enabled: bool = True
    port: int = 6881
    protocols: tuple = ("tcp", "udp")
    methods: tuple = ("upnp", "natpmp")
    manual: bool = False
    description: str = "OpenGrab"


def config_from_dict(data) -> PortMapConfig:
    """Build a config from settings, ignoring bad values (falls back to defaults)."""
    cfg = PortMapConfig()
    if not isinstance(data, dict):
        return cfg
    if isinstance(data.get("enabled"), bool):
        cfg.enabled = data["enabled"]
    if valid_port(data.get("port")):
        cfg.port = data["port"]
    protos = data.get("protocols")
    if isinstance(protos, (list, tuple)):
        cleaned = tuple(p.lower() for p in protos if isinstance(p, str) and p.lower() in PROTOCOLS)
        if cleaned:
            cfg.protocols = tuple(dict.fromkeys(cleaned))
    methods = data.get("methods")
    if isinstance(methods, (list, tuple)):
        cleaned = tuple(m.lower() for m in methods if isinstance(m, str) and m.lower() in METHODS)
        cfg.methods = tuple(dict.fromkeys(cleaned))
    if isinstance(data.get("manual"), bool):
        cfg.manual = data["manual"]
    if isinstance(data.get("description"), str) and data["description"].strip():
        cfg.description = data["description"].strip()
    return cfg


@dataclass
class MapResult:
    ok: bool
    method: str = ""
    internal_port: int = 0
    external_port: int = 0
    protocols: tuple = ()
    errors: list = field(default_factory=list)


class PortMapper:
    """Maps the listen port through the first working method.

    clients: dict of method name -> object with add_mapping(port, protocol, description)
    returning the external port (int), and remove_mapping(port, protocol).
    probe: optional callable(port) -> bool used by reachable().
    """

    def __init__(self, config: PortMapConfig, clients=None, probe=None):
        self.config = config
        self.clients = dict(clients or {})
        self.probe = probe
        self.active: MapResult | None = None

    def map(self) -> MapResult:
        cfg = self.config
        if not cfg.enabled:
            return MapResult(ok=False, errors=["disabled"])
        if not valid_port(cfg.port):
            return MapResult(ok=False, errors=["invalid port"])
        if cfg.manual:
            self.active = MapResult(True, "manual", cfg.port, cfg.port, tuple(cfg.protocols))
            return self.active
        errors = []
        for method in cfg.methods:
            client = self.clients.get(method)
            if client is None:
                errors.append(f"{method}: unavailable")
                continue
            done = []
            external = 0
            try:
                for proto in cfg.protocols:
                    ext = client.add_mapping(cfg.port, proto, cfg.description)
                    if not valid_port(ext):
                        raise ValueError(f"bad external port {ext!r}")
                    done.append(proto)
                    external = external or ext
            except Exception as exc:  # router errors must never crash the app
                errors.append(f"{method}: {exc}")
                for proto in done:
                    try:
                        client.remove_mapping(cfg.port, proto)
                    except Exception:
                        pass
                continue
            self.active = MapResult(True, method, cfg.port, external, tuple(done), errors)
            return self.active
        return MapResult(ok=False, internal_port=cfg.port, errors=errors or ["no methods"])

    def release(self) -> bool:
        """Remove the active mapping. Returns True when there was one to remove."""
        res = self.active
        if res is None:
            return False
        self.active = None
        client = self.clients.get(res.method)
        if client is not None:
            for proto in res.protocols:
                try:
                    client.remove_mapping(res.internal_port, proto)
                except Exception:
                    pass
        return True

    def reachable(self) -> bool | None:
        """Run the reachability test; None when there is no probe or no mapping."""
        if self.probe is None or self.active is None:
            return None
        try:
            return bool(self.probe(self.active.external_port))
        except Exception:
            return False