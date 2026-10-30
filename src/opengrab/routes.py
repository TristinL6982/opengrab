"""Small route registry for API features wired in after M12.

server.py keeps its original flat dispatchers; anything they do not handle
falls through to ``ApiServer.router``. A feature module registers handlers
with ``router.get/post/delete(pattern, fn, action=...)``. ``pattern`` is a
full-match regex on the routed path; named groups arrive as ``req.params``.

A handler receives a :class:`Request` and returns either a payload (sent as
200 JSON) or a ``(status, payload)`` tuple. When ``action`` is set, the
caller's scope must allow it (``remote.scope_allows``), so device tokens
from pairing cannot reach master-only routes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Request:
    method: str
    path: str
    params: dict
    query: dict
    body: dict
    srv: Any
    handler: Any = None
    scope: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def q(self, name: str, default: str = "") -> str:
        values = self.query.get(name) or [default]
        return str(values[0])


@dataclass
class Route:
    method: str
    pattern: "re.Pattern[str]"
    fn: Callable[[Request], Any]
    action: Optional[str] = None


class Router:
    def __init__(self) -> None:
        self._routes: list[Route] = []

    def add(self, method: str, pattern: str, fn, action: Optional[str] = None) -> None:
        self._routes.append(Route(method.upper(), re.compile(pattern), fn, action))

    def get(self, pattern: str, fn, action: Optional[str] = None) -> None:
        self.add("GET", pattern, fn, action)

    def post(self, pattern: str, fn, action: Optional[str] = None) -> None:
        self.add("POST", pattern, fn, action)

    def delete(self, pattern: str, fn, action: Optional[str] = None) -> None:
        self.add("DELETE", pattern, fn, action)

    def match(self, method: str, path: str):
        for route in self._routes:
            if route.method != method.upper():
                continue
            m = route.pattern.fullmatch(path)
            if m:
                return route, m.groupdict()
        return None, {}

    def paths(self) -> list[tuple[str, str]]:
        return [(r.method, r.pattern.pattern) for r in self._routes]

    def call(self, route: Route, req: Request) -> tuple[int, Any]:
        result = route.fn(req)
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], int):
            return result
        return 200, result
