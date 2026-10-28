"""Tests for logging and WebView2 modules."""

import os
import tempfile
from unittest.mock import patch

import pytest

from opengrab.logs import setup_logging
from opengrab.webview2 import webview2_version, ensure_webview2


def test_setup_logging():
    """Test setup_logging function."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Monkeypatch app_data_dir to use the temp dir
        with patch("opengrab.logs.app_data_dir") as mock_app_data_dir:
            mock_app_data_dir.return_value = tmpdir
            log_file = setup_logging()
            
            # Check that log file was created
            assert os.path.exists(log_file)
            
            # Write a test message
            import logging
            logger = logging.getLogger("opengrab.test")
            logger.warning("hello-m5c")
            
            # Flush handlers
            for handler in logging.getLogger().handlers:
                if handler.baseFilename == str(log_file):
                    handler.flush()
            
            # Read the log file
            with open(log_file, "r") as f:
                content = f.read()
                assert "hello-m5c" in content
            
            # Test that calling setup_logging twice doesn't add a second handler
            old_handlers = len(logging.getLogger().handlers)
            setup_logging()
            new_handlers = len(logging.getLogger().handlers)
            assert old_handlers == new_handlers
        
        # Clean up handlers
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            if handler.baseFilename == str(log_file):
                root_logger.removeHandler(handler)


def test_webview2_version_windows():
    """Test webview2_version on Windows."""
    with patch("opengrab.webview2.sys.platform", "win32"):
        # Test when winreg raises OSError
        with patch("opengrab.webview2.winreg.OpenKey") as mock_openkey:
            mock_openkey.side_effect = OSError()
            result = webview2_version()
            assert result is None


def test_webview2_version_non_windows():
    """Test webview2_version on non-Windows."""
    with patch("opengrab.webview2.sys.platform", "linux"):
        result = webview2_version()
        assert result is None


def test_ensure_webview2_returns_true():
    """Test ensure_webview2 returns True when version is available."""
    with patch("opengrab.webview2.os.name", "nt"):
        with patch("opengrab.webview2.webview2_version") as mock_version:
            mock_version.return_value = "120.0.0.0"
            result = ensure_webview2()
            assert result is True


def test_ensure_webview2_returns_false():
    """Test ensure_webview2 returns False when version is not available."""
    with patch("opengrab.webview2.os.name", "nt"):
        with patch("opengrab.webview2.webview2_version") as mock_version:
            mock_version.return_value = None
            # Mock MessageBox to avoid dialog
            with patch("opengrab.webview2.ctypes.windll.user32.MessageBoxW") as mock_msgbox:
                result = ensure_webview2()
                assert result is False
                mock_msgbox.assert_called_once()
