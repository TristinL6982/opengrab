"""M11 glue: build all queue/network feature configs from one settings section.

Each section is loaded with its own module's loader. A section with bad values
falls back to that module's defaults and its name is recorded in ``errors`` so
one broken entry never stops the app from starting.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from opengrab import avscan, portmap, reconnect, recovery, torrent
from opengrab.categories import CategoryRules

_LOADERS = {
    "reconnect": reconnect.config_from_dict,
    "retry": recovery.policy_from_dict,
    "torrent": torrent.settings_from_dict,
    "portmap": portmap.config_from_dict,
    "avscan": avscan.config_from_dict,
    "categories": CategoryRules.from_list,
}

SECTIONS = tuple(_LOADERS)


@dataclass
class NetworkConfig:
    reconnect: reconnect.ReconnectConfig
    retry: recovery.RetryPolicy
    torrent: torrent.TorrentSettings
    portmap: portmap.PortMapConfig
    avscan: avscan.ScanConfig
    categories: CategoryRules
    errors: list[str] = field(default_factory=list)


def load_network_config(data) -> NetworkConfig:
    data = data if isinstance(data, dict) else {}
    built: dict = {}
    errors: list[str] = []
    for name, loader in _LOADERS.items():
        try:
            built[name] = loader(data.get(name))
        except Exception:
            errors.append(name)
            built[name] = loader(None)
    return NetworkConfig(errors=errors, **built)


def load_network_config_file(path) -> NetworkConfig:
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return load_network_config({})
    except (OSError, ValueError):
        cfg = load_network_config({})
        cfg.errors.append("file")
        return cfg
    return load_network_config(raw)