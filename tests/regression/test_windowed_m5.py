import os
import sys
from pathlib import Path

import pytest


def test_windowed_exe_build():
    """Test that the build spec has proper windowed configuration and icon files."""
    # Read the spec file
    spec_path = Path("build/OpenGrab.spec")
    assert spec_path.exists(), "OpenGrab.spec should exist"
    
    with open(spec_path, 'r') as f:
        spec_content = f.read()
    
    # Check that console=False is set
    assert "console=False" in spec_content, "Build spec should have console=False"
    
    # Check that icon is set
    assert "opengrab_32.png" in spec_content, "Build spec should reference opengrab_32.png icon"
    
    # Check that assets are included in datas
    assert "opengrab/assets" in spec_content, "Build spec should include assets in datas"
    
    # Check that the icon files exist
    icon_path = Path("build/opengrab_32.png")
    assert icon_path.exists(), "Icon file should exist at build/opengrab_32.png"
    
    # Check that asset file exists
    asset_path = Path("src/opengrab/assets/icon.png")
    assert asset_path.exists(), "Asset file should exist at src/opengrab/assets/icon.png"


def test_windowed_stdio_handling(monkeypatch):
    """Test that CLI handles windowed stdio properly."""
    # Test with None stdout/stderr
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)
    
    # This should not crash - just make sure the handling code doesn't raise exceptions
    from opengrab.cli import main
    
    # Make sure that when we call main with --smoke-test it returns properly (0 or 1)
    result = main(["--smoke-test"])
    assert isinstance(result, int), "Main should return an integer exit code"
    assert result in [0, 1], "Exit code should be 0 or 1"
    
    # Restore to avoid affecting the rest of the tests
    monkeypatch.setattr(sys, 'stdout', sys.__stdout__)
    monkeypatch.setattr(sys, 'stderr', sys.__stderr__)