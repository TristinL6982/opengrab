"""OpenGrab main application bootstrap.

This module handles the initialization of all components:
settings, accounts, filters, registry, solver chain, manager, server.
"""

from typing import Optional
import json
import threading
import urllib.request
import secrets
import logging

_log = logging.getLogger("opengrab.app")

from .settings import load_settings, Settings
from .engine.manager import DownloadManager
from .server import ApiServer
from .single_instance import acquire, release, forward_to_running


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


def verify_own_server(port: int, nonce: str, base_path: str) -> bool:
    """Verify that the server running on port is the own one by checking its nonce.
    
    Args:
        port: Port number to check
        nonce: Expected nonce value from this server instance
        base_path: Base path prefix if needed
    
    Returns:
        True if the server response matches the expected nonce
        
    Raises:
        RuntimeError if a different server responds (on the same port)
    """
    import http.client
    import json
    
    # Build endpoint URL with base_path if needed
    path = "/api/ping"
    if base_path:
        path = f"{base_path}{path}"
        
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
        if resp.status == 200:
            parsed = json.loads(data)
            actual_nonce = parsed.get("nonce")
            if actual_nonce != nonce:
                raise RuntimeError(f"OpenGrab: port {port} is answered by another process")
            return True
        else:
            # Non-200 response, likely not our server running there
            raise RuntimeError(f"OpenGrab: port {port} is answered by another process")
    finally:
        conn.close()


def start_server(settings: Optional[Settings] = None,
                 host: Optional[str] = None, port: Optional[int] = None):
    """Start the download engine and local API, returning their handles."""
    settings = settings or load_settings()
    manager = DownloadManager(settings)
    server = ApiServer(settings, manager, host=host or settings.api_host, port=port if port is not None else settings.api_port)
    fell_back = False
    try:
        manager.start()
        try:
            bound_port = server.start()
        except OSError:
            # An explicitly requested CLI port must fail loudly.
            if port is not None or not settings.api_port:
                raise
            busy = settings.api_port
            server.stop()
            server = ApiServer(settings, manager, host=host or settings.api_host, port=0)
            bound_port = server.start()
            fell_back = True
            _log.warning("Configured port %s is busy; using port %s for this session (not saved)",
                         busy, bound_port)
        # Persist only when host and port came from settings and no fallback happened.
        if host is None and port is None and not fell_back:
            settings.api_host, settings.api_port = server.host, bound_port
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
    import pystray
    from PIL import Image, ImageDraw
    
    # Handle single instance lock for GUI mode  
    lock_handle = acquire()
    if lock_handle is None:
        # Another instance is running, forward URLs to it and exit
        try:
            from .settings import load_settings
            settings = load_settings()
            if forward_to_running(urls, settings.api_host, settings.api_port, settings.api_token, settings.base_path):
                print(f"Opened {len(urls)} URL(s) in existing OpenGrab instance")
            else:
                print("Failed to forward URLs to existing OpenGrab instance")
        except Exception:
            pass
        finally:
            release(lock_handle)
        return
    
    try:
        # Check WebView2 before starting the server
        from .webview2 import ensure_webview2
        if not ensure_webview2():
            release(lock_handle)
            return
            
        settings, manager, server, bound_port = start_server(settings, host, port)
        
        # Before creating the window, verify it's our own server instance
        verify_own_server(bound_port, server.nonce, settings.base_path)
        
        for url in urls:
            manager.add_url(url)
        stop_watcher = threading.Event()
        exiting = threading.Event()
        tray = None
        
        def watch_clipboard():
            endpoint = (f"http://127.0.0.1:{bound_port}{settings.base_path}"
                        "/api/clipboard")
            was_enabled = False
            previous = ""
            while not stop_watcher.wait(2):
                enabled = settings.clipboard_monitor_enabled
                if not enabled:
                    server.clipboard_status = "off"
                    was_enabled, previous = False, ""
                    continue
                try:
                    req = urllib.request.Request(endpoint, headers={"Authorization": f"Bearer {server.token}"})
                    with urllib.request.urlopen(req, timeout=5) as response:
                        current = json.loads(response.read()).get("text", "")
                    server.clipboard_status = "watching"
                    if not was_enabled:
                        previous, was_enabled = current, True
                        continue
                    if current != previous:
                        previous = current
                        server.capture_clipboard_text(current)
                except Exception:
                    # Keep the watcher alive if another app temporarily owns the
                    # clipboard or the desktop has no clipboard backend.
                    server.clipboard_status = "unavailable"
                    continue
        
        watcher = threading.Thread(target=watch_clipboard, daemon=True,
                                   name="opengrab-clipboard")
        try:
            window = webview.create_window(
                "OpenGrab", f"http://127.0.0.1:{bound_port}{settings.base_path}/#token={server.token}",
                min_size=(900, 600)
            )
            
            def on_closing():
                if exiting.is_set():
                    return None
                window.hide()
                return False
            
            def show_window(icon, item):
                window.show()
                window.restore()
            
            def toggle_monitor(icon, item):
                old_value = settings.clipboard_monitor_enabled
                settings.clipboard_monitor_enabled = not old_value
                try:
                    settings.save()
                except Exception:
                    settings.clipboard_monitor_enabled = old_value
                try:
                    icon.update_menu()
                except Exception:
                    pass
            
            def exit_app(icon, item):
                exiting.set()
                stop_watcher.set()
                icon.stop()
                window.destroy()
            
            window.events.closing += on_closing
            
            # Try to load icon from assets if it exists, otherwise generate default
            try:
                from pathlib import Path
                icon_path = Path(__file__).parent / "assets" / "icon.png"
                if icon_path.exists():
                    image = Image.open(icon_path)
                else:
                    image = Image.new("RGB", (64, 64), "#20242c")
                    draw = ImageDraw.Draw(image)
                    draw.ellipse((8, 8, 56, 56), fill="#38bdf8")
                    draw.polygon([(27, 17), (37, 17), (37, 34), (45, 34),
                                  (32, 49), (19, 34), (27, 34)], fill="#10212b")
            except Exception:
                # Fallback to default if there's any error loading the icon
                image = Image.new("RGB", (64, 64), "#20242c")
                draw = ImageDraw.Draw(image)
                draw.ellipse((8, 8, 56, 56), fill="#38bdf8")
                draw.polygon([(27, 17), (37, 17), (37, 34), (45, 34),
                              (32, 49), (19, 34), (27, 34)], fill="#10212b")
            
            tray = pystray.Icon(
                "OpenGrab", image, "OpenGrab",
                menu=pystray.Menu(
                    pystray.MenuItem("Open OpenGrab", show_window, default=True),
                    pystray.MenuItem("Monitor copied links", toggle_monitor,
                                     checked=lambda item: settings.clipboard_monitor_enabled),
                    pystray.MenuItem("Exit", exit_app),
                ),
            )
            tray.run_detached()
            watcher.start()
            webview.start()
        finally:
            stop_watcher.set()
            if watcher.is_alive():
                watcher.join(timeout=3)
            if tray is not None:
                try:
                    tray.stop()
                except Exception:
                    pass
            _close_server(manager, server)
    finally:
        release(lock_handle)


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
