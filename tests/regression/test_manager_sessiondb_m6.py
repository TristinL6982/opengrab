import tempfile
import os
from pathlib import Path
from opengrab.engine.manager import DownloadManager
from opengrab.models import Link, LinkStatus
from opengrab.settings import Settings


def test_save_and_load():
    """Test that _save_now then a new manager's _load_links restores the same link ids, urls and order, 
    and session.db exists while links.json does not get created."""
    # Create a temporary directory for the test
    temp_dir = Path(tempfile.mkdtemp())
    
    try:
        # Set up the first manager with some test data
        settings = Settings()
        settings.download_dir = str(temp_dir)
        
        manager1 = DownloadManager(settings)
        
        # Add some links
        link1 = Link(url="http://example.com/file1.zip", title="File 1")
        link2 = Link(url="http://example.com/file2.zip", title="File 2") 
        
        with manager1._lock:
            manager1._links[link1.id] = link1
            manager1._links[link2.id] = link2
        
        # Save using _save_now
        manager1._save_now()
        
        # Verify session.db was created and links.json is not created
        assert (temp_dir / "session.db").exists()
        assert not (temp_dir / "links.json").exists()
        
        # Create a new manager and load the data
        manager2 = DownloadManager(settings)
        manager2._load_links()
        
        # Verify that we loaded the same links
        assert len(manager2._links) == 2
        assert link1.id in manager2._links
        assert link2.id in manager2._links
        assert manager2._links[link1.id].url == "http://example.com/file1.zip"
        assert manager2._links[link2.id].url == "http://example.com/file2.zip"
        
    finally:
        # Cleanup
        for file in temp_dir.iterdir():
            if file.is_file():
                file.unlink()
        temp_dir.rmdir()


def test_downloading_link_fixup():
    """Test that a link saved with status DOWNLOADING comes back PAUSED with extra["note"] set."""
    # Create a temporary directory for the test
    temp_dir = Path(tempfile.mkdtemp())
    
    try:
        settings = Settings()
        settings.download_dir = str(temp_dir)
        
        manager1 = DownloadManager(settings)
        
        # Add a link with DOWNLOADING status
        link = Link(url="http://example.com/file.zip", title="File")
        link.status = LinkStatus.DOWNLOADING
        
        with manager1._lock:
            manager1._links[link.id] = link
            
        # Save using _save_now
        manager1._save_now()
        
        # Create a new manager and load the data
        manager2 = DownloadManager(settings)
        manager2._load_links()
        
        # Verify that the downloading link was fixed up to PAUSED with note
        assert len(manager2._links) == 1
        loaded_link = manager2._links[link.id]
        assert loaded_link.status == LinkStatus.PAUSED
        assert "note" in loaded_link.extra
        assert "was downloading when app closed" in loaded_link.extra["note"]
        
    finally:
        # Cleanup
        for file in temp_dir.iterdir():
            if file.is_file():
                file.unlink()
        temp_dir.rmdir()


def test_migration():
    """Test that migration works: write a links.json with two link dicts, call _load_links: 
    both links load, links.json.migrated exists, links.json is gone, and a fresh manager loads 
    both from session.db."""
    # Create a temporary directory for the test
    temp_dir = Path(tempfile.mkdtemp())
    
    try:
        settings = Settings()
        settings.download_dir = str(temp_dir)
        
        # Manually create a links.json file with two entries
        links_json_path = temp_dir / "links.json"
        content = '''[
    {
        "id": "link1",
        "url": "http://example.com/file1.zip", 
        "title": "File 1",
        "status": "WAITING"
    },
    {
        "id": "link2",
        "url": "http://example.com/file2.zip",
        "title": "File 2", 
        "status": "WAITING"
    }
]'''
        
        with open(links_json_path, 'w') as f:
            f.write(content)
            
        # Create manager
        manager = DownloadManager(settings)
        
        # Load should trigger migration
        manager._load_links()
        
        # Check that links.json.migrated exists and original links.json is gone
        assert not links_json_path.exists()  # Original should be renamed
        assert (temp_dir / "links.json.migrated").exists()
        
        # Check that we loaded both links properly
        assert len(manager._links) == 2
        assert "link1" in manager._links
        assert "link2" in manager._links
        
        # Verify data integrity
        assert manager._links["link1"].url == "http://example.com/file1.zip"
        assert manager._links["link2"].url == "http://example.com/file2.zip"
        
        # Create a fresh manager and verify it loads from session.db
        manager2 = DownloadManager(settings)
        manager2._load_links()
        
        assert len(manager2._links) == 2
        assert manager2._links["link1"].url == "http://example.com/file1.zip"
        assert manager2._links["link2"].url == "http://example.com/file2.zip"
        
    finally:
        # Cleanup
        for file in temp_dir.iterdir():
            if file.is_file():
                file.unlink()
        temp_dir.rmdir()


def test_shutdown_closes_db():
    """Test that shutdown() closes the DB (manager._db is None afterwards)."""
    # Create a temporary directory for the test
    temp_dir = Path(tempfile.mkdtemp())
    
    try:
        settings = Settings()
        settings.download_dir = str(temp_dir)
        
        manager = DownloadManager(settings)
        
        # Add some links to trigger database creation
        link = Link(url="http://example.com/file.zip", title="File")
        with manager._lock:
            manager._links[link.id] = link
        
        # Before shutdown, DB should be initialized
        manager._save_now()
        assert manager._db is not None
        
        # Shutdown should close the DB
        manager.shutdown()
        
        # After shutdown, DB should be closed
        assert manager._db is None
        
    finally:
        # Cleanup
        for file in temp_dir.iterdir():
            if file.is_file():
                file.unlink()
        temp_dir.rmdir()