"""Static import audit for the ``opengrab`` package.

Parses every ``.py`` file under ``src/opengrab`` with :mod:`ast`, builds the
intra-package import graph and prints the modules that are not reachable from
the runtime entry points (``app``, ``server``, ``cli``, ``smoke`` and
``__main__``).  Modules under ``plugins/builtin`` are treated as reachable
because :mod:`opengrab.plugins.registry` loads them dynamically.

Usage::

    python tools/import_audit.py            # list unreachable modules
    python tools/import_audit.py --verbose  # also show what each one imports
    python tools/import_audit.py --json     # machine readable output

The script has no third-party dependencies and never imports the package it
inspects, so it is safe to run without the project's virtualenv.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import deque
from pathlib import Path

PACKAGE = "opengrab"
ROOT_MODULES = ("app", "server", "cli", "smoke", "__main__")
DYNAMIC_PREFIXES = ("plugins.builtin.",)


def package_dir(repo_root: Path) -> Path:
    return repo_root / "src" / PACKAGE


def module_name(path: Path, pkg_dir: Path) -> str:
    """Return the dotted module name relative to the package (``""`` for the
    package ``__init__``)."""
    rel = path.relative_to(pkg_dir).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def discover(pkg_dir: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in sorted(pkg_dir.rglob("*.py")):
        modules[module_name(path, pkg_dir)] = path
    return modules


def _resolve_relative(current: str, is_package: bool, level: int, module: str | None) -> str:
    """Resolve a relative import to a package-relative dotted name."""
    parts = current.split(".") if current else []
    if not is_package and parts:
        parts = parts[:-1]
    drop = level - 1
    if drop > 0:
        parts = parts[:-drop] if drop <= len(parts) else []
    if module:
        parts = parts + module.split(".")
    return ".".join(parts)


def _strip_package(name: str) -> str | None:
    if name == PACKAGE:
        return ""
    prefix = PACKAGE + "."
    if name.startswith(prefix):
        return name[len(prefix):]
    return None


def imports_of(path: Path, name: str, known: set[str]) -> set[str]:
    """Return the package-relative module names imported by ``path``.

    ``from pkg.sub import name`` counts as an import of ``pkg.sub.name`` when
    that is a module, otherwise of ``pkg.sub``.  Relative imports are
    resolved against ``name``.  Imports inside functions count too, because
    the package uses lazy imports for optional dependencies.
    """
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    is_package = path.name == "__init__.py"
    found: set[str] = set()

    def add(candidate: str | None) -> None:
        if candidate is None:
            return
        # Walk up until we hit a module we know about (handles attribute
        # imports such as ``import opengrab.engine.manager``).
        while True:
            if candidate in known:
                found.add(candidate)
                return
            if "." not in candidate:
                return
            candidate = candidate.rsplit(".", 1)[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(_strip_package(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                base = _resolve_relative(name, is_package, node.level, node.module)
            else:
                base = _strip_package(node.module or "")
                if base is None:
                    continue
            add(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                child = f"{base}.{alias.name}" if base else alias.name
                if child in known:
                    found.add(child)
    found.discard(name)
    return found


def build_graph(modules: dict[str, Path]) -> dict[str, set[str]]:
    known = set(modules)
    return {name: imports_of(path, name, known) for name, path in modules.items()}


def reachable(graph: dict[str, set[str]], roots: list[str]) -> set[str]:
    seen: set[str] = set()
    queue: deque[str] = deque(roots)
    while queue:
        current = queue.popleft()
        if current in seen or current not in graph:
            continue
        seen.add(current)
        # Importing ``a.b.c`` also executes ``a/__init__`` and ``a/b/__init__``.
        parts = current.split(".")
        for i in range(len(parts)):
            parent = ".".join(parts[:i])
            if parent in graph and parent not in seen:
                queue.append(parent)
        queue.extend(sorted(graph[current] - seen))
    return seen


def audit(repo_root: Path) -> dict:
    pkg_dir = package_dir(repo_root)
    modules = discover(pkg_dir)
    graph = build_graph(modules)
    roots = [m for m in ROOT_MODULES if m in graph]
    roots += [m for m in graph if m.startswith(DYNAMIC_PREFIXES)]
    live = reachable(graph, roots)
    unreachable = sorted(m for m in graph if m not in live and m != "")
    orphan_set = set(unreachable)
    detail = {}
    for name in unreachable:
        importers = sorted(src for src, deps in graph.items() if name in deps)
        detail[name] = {
            "file": modules[name].relative_to(repo_root).as_posix(),
            "imports_in_package": sorted(graph[name]),
            "imports_other_orphans": sorted(graph[name] & orphan_set),
            "imported_by": importers,
        }
    return {
        "package_dir": pkg_dir.relative_to(repo_root).as_posix(),
        "module_count": len(graph),
        "roots": roots,
        "reachable_count": len(live),
        "unreachable": unreachable,
        "detail": detail,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="repository root (default: parent of tools/)")
    parser.add_argument("--verbose", action="store_true",
                        help="show intra-package imports and importers of each orphan")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    result = audit(args.root)
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(f"package: {result['package_dir']}  modules: {result['module_count']}  "
          f"reachable: {result['reachable_count']}  unreachable: {len(result['unreachable'])}")
    static_roots = [r for r in result["roots"] if not r.startswith(DYNAMIC_PREFIXES)]
    print("roots: " + ", ".join(static_roots) + " (+ plugins/builtin/* loaded dynamically)")
    print()
    print("Modules not reachable from any runtime entry point:")
    orphans = set(result["unreachable"])
    for name in result["unreachable"]:
        info = result["detail"][name]
        print(f"  {name}")
        if args.verbose:
            if info["imports_in_package"]:
                print(f"      imports:     {', '.join(info['imports_in_package'])}")
            if info["imported_by"]:
                suffix = " (all orphans)" if set(info["imported_by"]) <= orphans else ""
                print(f"      imported by: {', '.join(info['imported_by'])}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
