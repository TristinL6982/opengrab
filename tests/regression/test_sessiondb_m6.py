import tempfile
import os
import sqlite3
from opengrab.sessiondb import SessionDB


def test_journal_mode():
    """Test that journal mode is WAL."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        with SessionDB(db_path) as db:
            assert db.journal_mode() == "wal"
    finally:
        os.unlink(db_path)


def test_schema_version():
    """Test that schema version is stored as '1' in meta."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        with SessionDB(db_path) as db:
            # Check that schema_version exists and is 1
            cursor = db._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
            result = cursor.fetchone()
            assert result is not None
            assert result[0] == "1"
    finally:
        os.unlink(db_path)


def test_upsert_then_load():
    """Test that upsert then load round-trips nested dict data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        with SessionDB(db_path) as db:
            # Insert a package with nested data
            test_data = {
                "name": "test_pkg",
                "nested": {
                    "key1": "value1",
                    "key2": [1, 2, 3],
                    "deep": {
                        "inner": "data"
                    }
                }
            }
            db.upsert_package("pkg1", test_data, 0)
            
            # Load and verify
            result = db.load()
            assert len(result) == 1
            assert result[0]["id"] == "pkg1"
            assert result[0]["data"] == test_data
    finally:
        os.unlink(db_path)


def test_upsert_updates_existing():
    """Test that upsert of an existing id updates data and position."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        with SessionDB(db_path) as db:
            # Insert a package
            db.upsert_package("pkg1", {"name": "old"}, 0)
            
            # Update the package
            db.upsert_package("pkg1", {"name": "new"}, 1)
            
            # Load and verify
            result = db.load()
            assert len(result) == 1
            assert result[0]["data"]["name"] == "new"
            assert result[0]["id"] == "pkg1"
    finally:
        os.unlink(db_path)


def test_ordering():
    """Test that packages and links come back ordered by position."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        with SessionDB(db_path) as db:
            # Insert packages in reverse order
            db.upsert_package("pkg3", {"id": "pkg3"}, 2)
            db.upsert_package("pkg1", {"id": "pkg1"}, 0)
            db.upsert_package("pkg2", {"id": "pkg2"}, 1)
            
            # Insert links for package 1 
            db.upsert_link("link3", "pkg1", {"id": "link3"}, 2)
            db.upsert_link("link1", "pkg1", {"id": "link1"}, 0)
            db.upsert_link("link2", "pkg1", {"id": "link2"}, 1)
            
            # Load and verify ordering
            result = db.load()
            assert len(result) == 3
            
            # Check packages ordered by position
            assert result[0]["id"] == "pkg1"
            assert result[1]["id"] == "pkg2" 
            assert result[2]["id"] == "pkg3"
            
            # Check links ordered by position for first package
            assert len(result[0]["links"]) == 3
            assert result[0]["links"][0]["id"] == "link1"
            assert result[0]["links"][1]["id"] == "link2"
            assert result[0]["links"][2]["id"] == "link3"
    finally:
        os.unlink(db_path)


def test_delete_package():
    """Test that delete_package returns True and removes links (cascade)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        with SessionDB(db_path) as db:
            # Insert a package with links
            db.upsert_package("pkg1", {"id": "pkg1"}, 0)
            db.upsert_link("link1", "pkg1", {"id": "link1"}, 0)
            db.upsert_link("link2", "pkg1", {"id": "link2"}, 1)
            
            # Delete package and verify it returns True
            assert db.delete_package("pkg1") == True
            
            # Verify package is gone and links are gone (cascade)
            result = db.load()
            assert len(result) == 0
            
            # Try to delete non-existent package
            assert db.delete_package("nonexistent") == False
    finally:
        os.unlink(db_path)


def test_delete_link():
    """Test that delete_link returns True/False."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        with SessionDB(db_path) as db:
            # Insert a package with a link
            db.upsert_package("pkg1", {"id": "pkg1"}, 0)
            db.upsert_link("link1", "pkg1", {"id": "link1"}, 0)
            
            # Delete link and verify it returns True
            assert db.delete_link("link1") == True
            
            # Try to delete non-existent link
            assert db.delete_link("nonexistent") == False
    finally:
        os.unlink(db_path)


def test_persistence():
    """Test that data survives close and reopen of the same file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        # Insert data
        with SessionDB(db_path) as db:
            db.upsert_package("pkg1", {"name": "test"}, 0)
        
        # Reopen and verify data is still there
        with SessionDB(db_path) as db:
            result = db.load()
            assert len(result) == 1
            assert result[0]["data"]["name"] == "test"
    finally:
        os.unlink(db_path)


def test_replace_all():
    """Test that replace_all replaces everything and load matches the input."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        with SessionDB(db_path) as db:
            # Insert initial data
            db.upsert_package("pkg1", {"name": "old"}, 0)
            
            # Replace with new data
            new_data = [
                {
                    "id": "pkg_new1",
                    "data": {"name": "new1"},
                    "links": [
                        {"id": "link_new1", "data": {"name": "link1"}}
                    ]
                },
                {
                    "id": "pkg_new2",
                    "data": {"name": "new2"},
                    "links": []
                }
            ]
            
            db.replace_all(new_data)
            
            # Verify load matches input
            result = db.load()
            assert len(result) == 2
            assert result[0]["id"] == "pkg_new1"
            assert result[0]["data"]["name"] == "new1"
            assert len(result[0]["links"]) == 1
            assert result[0]["links"][0]["id"] == "link_new1"
            assert result[1]["id"] == "pkg_new2"
            assert result[1]["data"]["name"] == "new2"
    finally:
        os.unlink(db_path)


def test_replace_all_invalid_data():
    """Test that replace_all with a bad entry raises and leaves previous contents unchanged."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        with SessionDB(db_path) as db:
            # Insert initial data
            db.upsert_package("pkg1", {"name": "test"}, 0)
            
            # Try to replace with invalid JSON data
            bad_data = [
                {
                    "id": "pkg_new1",
                    "data": {"name": "new1"},
                    "links": [
                        {"id": "link_bad", "data": {"x": object()}}  # This can't be serialized
                    ]
                }
            ]
            
            # This should raise an exception
            try:
                db.replace_all(bad_data)
                assert False, "Should have raised an exception"
            except (TypeError, ValueError):
                # Expected - JSON serialization failed
                pass
            
            # Verify old data is still there
            result = db.load()
            assert len(result) == 1
            assert result[0]["id"] == "pkg1"
    finally:
        os.unlink(db_path)


def test_schema_version_error():
    """Test that opening a file whose meta schema_version is '99' raises RuntimeError."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        # Create database and set high schema version
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '99')")
        conn.commit()
        conn.close()
        
        # This should raise RuntimeError
        try:
            with SessionDB(db_path) as db:
                pass # This code won't be reached
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "newer than supported version" in str(e)
    finally:
        # Ensure we don't have permission issues when deleting
        try:
            os.unlink(db_path)
        except PermissionError:
            pass  # File might still be locked, which is OK for this test


def test_concurrent_access():
    """Test that two threads each doing 50 upserts leave 100 links."""
    import threading
    import time
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    try:
        def thread_worker(db_path, start_id):
            with SessionDB(db_path) as db:
                for i in range(50):
                    pkg_id = f"pkg_{start_id + i}"
                    link_id = f"link_{start_id + i}"
                    db.upsert_package(pkg_id, {"id": pkg_id}, 0)
                    db.upsert_link(link_id, pkg_id, {"id": link_id}, 0)
        
        # Use threading to test concurrent access
        t1 = threading.Thread(target=thread_worker, args=(db_path, 0))
        t2 = threading.Thread(target=thread_worker, args=(db_path, 50))
        
        t1.start()
        t2.start()
        
        t1.join()
        t2.join()
        
        # Verify we have 100 packages and 100 links
        with SessionDB(db_path) as db:
            result = db.load()
            assert len(result) == 100
            
            # All packages should have one link each
            total_links = sum(len(pkg["links"]) for pkg in result)
            assert total_links == 100
        
    finally:
        os.unlink(db_path)