"""Command-line entry point for OpenGrab."""
from __future__ import annotations

import argparse
import os
import sys

def _ensure_stdio() -> None:
    """Windowed exe (console=False): sys.stdout/stderr are None, so print() and logging must not crash."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


_ensure_stdio()

from . import __version__
from .settings import load_settings
from .logs import setup_logging


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opengrab",
        description="OpenGrab: a local download manager with a web interface",
    )
    parser.add_argument("--version", action="version",
                        version=f"OpenGrab {__version__}")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true", help="open the desktop window (default)")
    mode.add_argument("--headless", action="store_true", help="serve the web UI without a desktop window")
    parser.add_argument("--port", "-p", type=int, default=None,
                        help="API port (default: saved setting or 5678)")
    parser.add_argument("--bind", "-b", default=None,
                        help="bind address (default: saved setting or 127.0.0.1)")
    parser.add_argument("--data-dir", default=None,
                        help="directory for settings, queue state, and plugins")
    parser.add_argument("--grab", action="append", default=[], metavar="URL",
                        help="add a URL on startup; may be repeated")
    parser.add_argument("--list-plugins", action="store_true",
                        help="list discovered plugins and exit")
    parser.add_argument("--pair", action="store_true",
                        help="with --headless, print a one-time device pairing code at startup")
    parser.add_argument("--smoke-test", action="store_true",
                        help="run the OpenGrab smoke check and exit")
    parser.add_argument("--validate-template", metavar="FILE", default=None,
                        help="check a plugin template (.json) and exit 1 if it has errors")
    parser.add_argument("--minimized", action="store_true",
                        help="start in the tray without showing the window (used by autostart)")
    parser.add_argument("targets", nargs="*", metavar="LINK_OR_FILE",
                        help="links, magnet:/opengrab:// links or .torrent/.crawljob files to add")
    return parser


def _client_commands():
    from .client_cli import COMMANDS
    return COMMANDS


def main(argv=None) -> int:
    _ensure_stdio()
    raw = list(sys.argv[1:] if argv is None else argv)
    launcher_only = {"--gui", "--headless", "--smoke-test", "--list-plugins", "--validate-template",
                     "--version", "--pair", "--minimized"}
    if any(a in _client_commands() for a in raw) and not launcher_only & set(raw):
        # M13: opengrab add|list|status|pause|resume|start|stop|remove|notifications
        from .client_cli import run as run_client
        return run_client(raw)
    args = _parser().parse_args(argv)
    if args.validate_template:  # W12: plugin SDK
        from .plugins.sdk import validate_template_file
        errors = validate_template_file(args.validate_template)
        for e in errors:
            print(f"error: {e}")
        print("template OK" if not errors else f"{len(errors)} problem(s)")
        return 1 if errors else 0
    if args.data_dir:
        os.environ["OPENGRAB_DATA_DIR"] = args.data_dir
    settings = load_settings()
    if os.environ.get("OPENGRAB_BASE_PATH") is not None:
        settings.base_path = os.environ["OPENGRAB_BASE_PATH"]
    host = (args.bind or os.environ.get("OPENGRAB_BIND")
            or os.environ.get("OPENGRAB_API_HOST") or settings.api_host)
    try:
        port = args.port if args.port is not None else int(
            os.environ.get("OPENGRAB_PORT",
                           os.environ.get("OPENGRAB_API_PORT", settings.api_port))
        )
    except ValueError:
        print("Error: OPENGRAB_PORT must be an integer", file=sys.stderr)
        return 2
    if not 1 <= port <= 65535:
        print("Error: --port must be between 1 and 65535", file=sys.stderr)
        return 2

    # Setup logging
    try:
        if not getattr(args, "smoke_test", False):
            setup_logging()
    except Exception:
        pass  # Continue even if logging fails

    try:
        if args.smoke_test:
            from .smoke import run_smoke_test
            return 0 if run_smoke_test() else 1
        if args.list_plugins:
            from .engine.manager import DownloadManager
            manager = DownloadManager(settings)
            try:
                manager.start()
                for plugin in manager.registry.snapshot():
                    print(f"{plugin['id']}: {plugin['name']} — {plugin['description']}")
                return 0
            finally:
                manager.shutdown()

        from .app import run_gui_mode, run_headless_mode
        from .winintegration import split_targets
        urls, files = split_targets(args.targets)
        urls = list(args.grab) + urls
        extra = {"files": files} if files else {}
        if args.headless:
            run_headless_mode(settings, host=host, port=port, urls=urls,
                              pair=args.pair, **extra)
        else:
            if args.minimized:
                extra["minimized"] = True
            run_gui_mode(settings, host=host, port=port, urls=urls, **extra)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"OpenGrab failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
