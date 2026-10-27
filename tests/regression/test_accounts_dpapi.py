"""Test account management with DPAPI protection."""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

# Add src to path like in test_api_security.py
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from opengrab.accounts import AccountManager


def test_account_encryption():
    """Test that passwords are encrypted with DPAPI on Windows or obfuscated otherwise."""
    # Set up temporary data directory for each test to avoid interference
    tmp_dir = tempfile.mkdtemp(prefix="og-acc-")
    os.environ["OPENGRAB_DATA_DIR"] = tmp_dir
    
    try:
        mgr = AccountManager()
        mgr.load()  # Load any existing accounts (should be empty)
        
        # Add an account
        acc = mgr.add("example.com", "user", "s3cret")
        
        # Read raw accounts.json to verify encryption
        with open(mgr._path, 'r') as f:
            raw_content = f.read()
            
        # Verify password is not in plain text
        assert "s3cret" not in raw_content
        
        # Parse the JSON
        data = json.loads(raw_content)
        assert len(data) == 1
        password_field = data[0]['password']
        
        # On Windows, should start with "dp:", otherwise "ob:"
        if sys.platform == "win32":
            assert password_field.startswith("dp:")
        else:
            assert password_field.startswith("ob:")
    finally:
        # Cleanup
        if os.path.exists(tmp_dir):
            import shutil
            shutil.rmtree(tmp_dir)


def test_account_decryption():
    """Test that accounts load and decrypt correctly, including double-encoded files."""
    tmp_dir = tempfile.mkdtemp(prefix="og-acc-")
    os.environ["OPENGRAB_DATA_DIR"] = tmp_dir
    
    try:
        mgr = AccountManager()
        mgr.load()  # Load any existing accounts (should be empty)
        
        # Add an account
        acc = mgr.add("example.com", "user", "s3cret")
        
        # Reload the manager to test decryption
        mgr2 = AccountManager()
        mgr2.load()
        
        # Verify we can get the decrypted password
        assert mgr2.decrypted_password(mgr2.accounts[0]) == "s3cret"
        
        # Test that loading twice doesn't double-encode (no regression)
        mgr3 = AccountManager()
        mgr3.load()
        assert mgr3.decrypted_password(mgr3.accounts[0]) == "s3cret"
    finally:
        # Cleanup
        if os.path.exists(tmp_dir):
            import shutil
            shutil.rmtree(tmp_dir)


def test_double_encoded_file():
    """Test loading a file with passwords already double-encoded by the old bug."""
    tmp_dir = tempfile.mkdtemp(prefix="og-acc-")
    os.environ["OPENGRAB_DATA_DIR"] = tmp_dir
    
    try:
        # Create a mock entry with double-encoded password
        raw_data = [{
                'id': 'A12345678901',
                'site': 'example.com',
                'username': 'user', 
                'password': 'ob:' + base64.b64encode(b'ob:' + base64.b64encode(b'pw')).decode(),
                'premium': False,
                'data': {},
                'added': 1234567890.0
            }]
        
        mgr = AccountManager()
        mgr.load()  # Load any existing accounts (should be empty)
        
        # Save to file directly (not using the manager's save method which re-encodes)
        with open(mgr._path, 'w') as f:
            json.dump(raw_data, f)
            
        # Load and verify it gets properly decoded (max 5 times as implemented)
        mgr.load()  # This loads and should automatically fix double-encoded passwords
        
        # Should be properly decrypted now
        assert len(mgr.accounts) == 1
        assert mgr.decrypted_password(mgr.accounts[0]) == "pw"
    finally:
        # Cleanup
        if os.path.exists(tmp_dir):
            import shutil
            shutil.rmtree(tmp_dir)


def test_snapshot():
    """Test that snapshot() doesn't include passwords."""
    tmp_dir = tempfile.mkdtemp(prefix="og-acc-")
    os.environ["OPENGRAB_DATA_DIR"] = tmp_dir
    
    try:
        mgr = AccountManager()
        mgr.load()  # Load any existing accounts (should be empty)
        
        # Add an account
        acc = mgr.add("example.com", "user", "s3cret")
        
        snap = mgr.snapshot()
        assert len(snap) == 1
        assert snap[0]["password"] == ""
        assert snap[0]["has_password"] is True
        
        # Verify the password isn't in the snapshot at all
        assert "s3cret" not in str(snap)
    finally:
        # Cleanup
        if os.path.exists(tmp_dir):
            import shutil
            shutil.rmtree(tmp_dir)


def test_account_removal():
    """Test that account removal works correctly."""
    tmp_dir = tempfile.mkdtemp(prefix="og-acc-")
    os.environ["OPENGRAB_DATA_DIR"] = tmp_dir
    
    try:
        mgr = AccountManager()
        mgr.load()  # Load any existing accounts (should be empty)
        
        # Add an account
        acc = mgr.add("example.com", "user", "s3cret")
        
        # Remove the account
        removed = mgr.remove(acc.id)
        
        # Should be removed
        assert removed is True
        
        # Reload and verify it's gone
        mgr2 = AccountManager()
        mgr2.load()
        
        assert len(mgr2.accounts) == 0
        
        # Check that it's actually gone from file
        with open(mgr._path, 'r') as f:
            content = f.read()
            assert acc.id not in content
    finally:
        # Cleanup
        if os.path.exists(tmp_dir):
            import shutil
            shutil.rmtree(tmp_dir)
