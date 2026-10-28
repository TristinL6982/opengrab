import sqlite3
import json
import threading
import time

SCHEMA_VERSION = 1


class SessionDB:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._conn = None
        
        # Open connection and set up database
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        
        # Create tables if they don't exist
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS packages (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                position INTEGER NOT NULL,
                updated REAL NOT NULL
            )
        """)
        
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS links (
                id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
                data TEXT NOT NULL,
                position INTEGER NOT NULL,
                updated REAL NOT NULL
            )
        """)
        
        # Check schema version on open
        cursor = self._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        result = cursor.fetchone()
        if result:
            existing_version = int(result[0])
            if existing_version > SCHEMA_VERSION:
                raise RuntimeError(f"Database schema version {existing_version} is newer than supported version {SCHEMA_VERSION}")
        
        # Initialize schema version if not present
        if not result:
            self._conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        
        self._conn.commit()
    
    def journal_mode(self) -> str:
        """Return the current journal mode of the database."""
        with self._lock:
            cursor = self._conn.execute("PRAGMA journal_mode")
            return cursor.fetchone()[0].lower()
    
    def upsert_package(self, pkg_id: str, data: dict, position: int) -> None:
        """Upsert a package into the database."""
        with self._lock:
            updated = time.time()
            json_data = json.dumps(data, sort_keys=True)
            self._conn.execute(
                "INSERT OR REPLACE INTO packages (id, data, position, updated) VALUES (?, ?, ?, ?)",
                (pkg_id, json_data, position, updated)
            )
            self._conn.commit()
    
    def upsert_link(self, link_id: str, package_id: str, data: dict, position: int) -> None:
        """Upsert a link into the database."""
        with self._lock:
            updated = time.time()
            json_data = json.dumps(data, sort_keys=True)
            self._conn.execute(
                "INSERT OR REPLACE INTO links (id, package_id, data, position, updated) VALUES (?, ?, ?, ?, ?)",
                (link_id, package_id, json_data, position, updated)
            )
            self._conn.commit()
    
    def delete_package(self, pkg_id: str) -> bool:
        """Delete a package and its associated links. Returns True if a row was deleted."""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM packages WHERE id = ?", (pkg_id,))
            affected = cursor.rowcount
            self._conn.commit()
            return affected > 0
    
    def delete_link(self, link_id: str) -> bool:
        """Delete a link. Returns True if a row was deleted."""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM links WHERE id = ?", (link_id,))
            affected = cursor.rowcount
            self._conn.commit()
            return affected > 0
    
    def load(self) -> list[dict]:
        """Load all packages and their links from the database."""
        with self._lock:
            # Get packages ordered by position then id
            packages_cursor = self._conn.execute("""
                SELECT id, data, position, updated FROM packages 
                ORDER BY position, id
            """)
            
            result = []
            for pkg_row in packages_cursor.fetchall():
                pkg_id, pkg_data, position, updated = pkg_row
                
                # Get links for this package ordered by position then id
                links_cursor = self._conn.execute("""
                    SELECT id, data, position, updated FROM links 
                    WHERE package_id = ? 
                    ORDER BY position, id
                """, (pkg_id,))
                
                links = []
                for link_row in links_cursor.fetchall():
                    link_id, link_data, link_position, link_updated = link_row
                    links.append({
                        "id": link_id,
                        "data": json.loads(link_data)
                    })
                
                result.append({
                    "id": pkg_id,
                    "data": json.loads(pkg_data),
                    "links": links
                })
            
            return result
    
    def replace_all(self, packages: list[dict]) -> None:
        """Replace all data in the database with the given list of packages."""
        with self._lock:
            try:
                # Start transaction
                self._conn.execute("BEGIN IMMEDIATE")
                
                # Delete all existing data
                self._conn.execute("DELETE FROM links")
                self._conn.execute("DELETE FROM packages")
                
                # Insert new data
                for i, pkg in enumerate(packages):
                    pkg_id = pkg["id"]
                    pkg_data = pkg["data"]
                    pkg_links = pkg.get("links", [])
                    
                    # Insert package
                    json_pkg_data = json.dumps(pkg_data, sort_keys=True)
                    self._conn.execute(
                        "INSERT INTO packages (id, data, position, updated) VALUES (?, ?, ?, ?)",
                        (pkg_id, json_pkg_data, i, time.time())
                    )
                    
                    # Insert links
                    for j, link in enumerate(pkg_links):
                        link_id = link["id"]
                        link_data = link["data"]
                        json_link_data = json.dumps(link_data, sort_keys=True)
                        
                        self._conn.execute(
                            "INSERT INTO links (id, package_id, data, position, updated) VALUES (?, ?, ?, ?, ?)",
                            (link_id, pkg_id, json_link_data, j, time.time())
                        )
                
                # Commit transaction
                self._conn.commit()
            except Exception:
                # Rollback on error
                self._conn.rollback()
                raise
    
    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()