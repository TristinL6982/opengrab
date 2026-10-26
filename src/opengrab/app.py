"""OpenGrab main application bootstrap.

This module handles the initialization of all components:
settings, accounts, filters, registry, solver chain, manager, server.
"""

from typing import Optional

from .settings import load_settings, Settings
from .engine.manager import DownloadManager
from .server import ApiServer


def bootstrap_app(settings: Optional[Settings] = None) -> tuple:
    """Bootstrap the OpenGrab application.
    
    Returns:
        Tuple of (settings, accounts, filters, registry, solver_chain, manager)
    """
    if settings is None:
        settings = load_settings()
    
    manager = DownloadManager(settings)
    return (settings, manager.accounts, manager.filters, manager.registry,
            manager.captcha_chain, manager)


def start_server(settings: Optional[Settings] = None,
                 host: Optional[str] = None, port: Optional[int] = None):
    """Start the download engine and local API, returning their handles."""
    settings = settings or load_settings()
    host = host or settings.api_host
    port = settings.api_port if port is None else port
    manager = DownloadManager(settings)
    server = ApiServer(settings, manager, host=host, port=port)
    try:
        manager.start()
        bound_port = server.start()
        settings.api_host, settings.api_port = host, bound_port
        settings.save()
        return settings, manager, server, bound_port
    except Exception:
        server.stop()
        manager.shutdown()
        raise


def _close_server(manager: DownloadManager, server: ApiServer) -> None:
    server.stop()
    manager.shutdown()


def run_gui_mode(settings: Optional[Settings] = None,
                 host: Optional[str] = None, port: Optional[int] = None,
                 urls=()) -> None:
    """Run the local web UI in a native pywebview window."""
    import webview

    settings, manager, server, bound_port = start_server(settings, host, port)
    for url in urls:
        manager.add_url(url)
    try:
        webview.create_window(
            "OpenGrab", f"http://127.0.0.1:{bound_port}/", min_size=(900, 600)
        )
        webview.start()
    finally:
        _close_server(manager, server)


def run_headless_mode(settings: Optional[Settings] = None,
                      host: Optional[str] = None, port: Optional[int] = None,
                      urls=()) -> None:
    """Run the API and web UI until interrupted."""
    settings, manager, server, bound_port = start_server(settings, host, port)
    for url in urls:
        manager.add_url(url)
    print(f"OpenGrab API -> http://{server.host}:{bound_port}")
    try:
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        _close_server(manager, server)


def main():
    """Main CLI entry point."""
    from .cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    main()
