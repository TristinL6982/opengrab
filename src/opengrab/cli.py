"""Command-line entry point for OpenGrab."""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .settings import load_settings


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
    parser.add_argument("--smoke-test", action="store_true",
                        help="run the OpenGrab smoke check and exit")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.data_dir:
        os.environ["OPENGRAB_DATA_DIR"] = args.data_dir
    settings = load_settings()
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
        runner = run_headless_mode if args.headless else run_gui_mode
        runner(settings, host=host, port=port, urls=args.grab)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"OpenGrab failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
