from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .models import ZoteroItem


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    key TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT,
    creators TEXT,
    tags TEXT,
    collections TEXT,
    year INTEGER,
    doi TEXT,
    url TEXT,
    raw_json TEXT NOT NULL,
    content_hash TEXT,
    embedding BLOB,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zotero_collections (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_key TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_items_version ON items(version);
CREATE INDEX IF NOT EXISTS idx_collections_parent ON zotero_collections(parent_key);
"""


class ProfileStorage:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def initialize(self) -> None:
        conn = self.connect()
        
        # Check if we need to migrate from old schema
        try:
            # Try to get existing table info
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items'")
            if cursor.fetchone():
                # Table exists, check if it has the version column
                cursor = conn.execute("PRAGMA table_info(items)")
                columns = [row[1] for row in cursor.fetchall()]
                
                if 'version' not in columns:
                    # Old schema detected, need to migrate
                    import logging
                    logging.warning("Old database schema detected. Starting migration...")
                    
                    # Get old columns dynamically
                    old_columns = columns
                    
                    # Backup old table
                    conn.execute("ALTER TABLE items RENAME TO items_old")
                    
                    # Create new schema
                    conn.executescript(SCHEMA)
                    
                    # Build migration SQL based on available columns
                    # Required new columns: key, version, title, raw_json
                    # Optional columns that may exist: abstract, creators, tags, collections, year, doi, url, content_hash, embedding, updated_at
                    new_required = ['key', 'version', 'title', 'abstract', 'creators', 'tags', 'collections', 
                                   'year', 'doi', 'url', 'raw_json', 'content_hash', 'embedding', 'updated_at']
                    
                    # Build SELECT clause with available columns
                    select_parts = []
                    for col in new_required:
                        if col == 'version':
                            select_parts.append('0 as version')  # Default version for old items
                        elif col == 'collections':
                            if 'collections' in old_columns:
                                select_parts.append('collections')
                            else:
                                select_parts.append("'[]' as collections")  # Default empty array
                        elif col in old_columns:
                            select_parts.append(col)
                        else:
                            # Column doesn't exist in old schema, use NULL or default
                            select_parts.append(f'NULL as {col}')
                    
                    # Migrate data
                    migration_sql = f"""
                        INSERT INTO items ({', '.join(new_required)})
                        SELECT {', '.join(select_parts)}
                        FROM items_old
                    """
                    
                    try:
                        conn.execute(migration_sql)
                        # Drop old table after successful migration
                        conn.execute("DROP TABLE items_old")
                        logging.info("Database migration completed successfully")
                    except sqlite3.Error as migrate_error:
                        logging.error(f"Migration failed: {migrate_error}")
                        logging.warning("Dropping old data and starting fresh")
                        # If migration fails, just drop old table and start fresh
                        conn.execute("DROP TABLE IF EXISTS items_old")
                else:
                    # New schema already exists, just ensure all tables are created
                    conn.executescript(SCHEMA)
            else:
                # No table exists, create from scratch
                conn.executescript(SCHEMA)
                
        except sqlite3.Error as e:
            # If any error occurs, try to create schema from scratch
            import logging
            logging.error(f"Error during database initialization: {e}")
            logging.info("Creating schema from scratch...")
            # Drop any problematic tables
            try:
                conn.execute("DROP TABLE IF EXISTS items_old")
                conn.execute("DROP TABLE IF EXISTS items")
            except:
                pass
            conn.executescript(SCHEMA)
        
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # metadata helpers
    def get_metadata(self, key: str) -> Optional[str]:
        cur = self.connect().execute("SELECT value FROM metadata WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    def set_metadata(self, key: str, value: str) -> None:
        self.connect().execute(
            "REPLACE INTO metadata(key, value) VALUES(?, ?)",
            (key, value),
        )
        self.connect().commit()

    def last_modified_version(self) -> Optional[int]:
        value = self.get_metadata("last_modified_version")
        return int(value) if value else None

    def set_last_modified_version(self, version: int) -> None:
        self.set_metadata("last_modified_version", str(version))

    # item helpers
    def upsert_item(self, item: ZoteroItem, content_hash: Optional[str] = None) -> None:
        data = (
            item.key,
            item.version,
            item.title,
            item.abstract,
            json.dumps(item.creators),
            json.dumps(item.tags),
            json.dumps(item.collections),
            item.year,
            item.doi,
            item.url,
            json.dumps(item.raw),
            content_hash,
        )
        self.connect().execute(
            """
            INSERT INTO items(
                key, version, title, abstract, creators, tags, collections, year, doi, url, raw_json, content_hash
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                version=excluded.version,
                title=excluded.title,
                abstract=excluded.abstract,
                creators=excluded.creators,
                tags=excluded.tags,
                collections=excluded.collections,
                year=excluded.year,
                doi=excluded.doi,
                url=excluded.url,
                raw_json=excluded.raw_json,
                content_hash=excluded.content_hash,
                updated_at=CURRENT_TIMESTAMP
            """,
            data,
        )
        self.connect().commit()

    def remove_items(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        if not keys:
            return
        placeholders = ",".join("?" for _ in keys)
        self.connect().execute(f"DELETE FROM items WHERE key IN ({placeholders})", keys)
        self.connect().commit()

    def clear_all_items(self) -> None:
        """清空所有条目（用于全量同步时重建）"""
        conn = self.connect()
        conn.execute("DELETE FROM items")
        conn.commit()

    def set_embedding(self, key: str, vector: bytes) -> None:
        self.connect().execute(
            "UPDATE items SET embedding = ?, updated_at=CURRENT_TIMESTAMP WHERE key = ?",
            (vector, key),
        )
        self.connect().commit()

    def iter_items(self) -> Iterable[ZoteroItem]:
        cur = self.connect().execute("SELECT * FROM items")
        for row in cur:
            yield _row_to_item(row)

    def fetch_items_without_embedding(self) -> List[Tuple[ZoteroItem, Optional[str]]]:
        cur = self.connect().execute(
            "SELECT * FROM items WHERE embedding IS NULL ORDER BY updated_at ASC"
        )
        rows = cur.fetchall()
        return [(_row_to_item(row), row["content_hash"]) for row in rows]

    def fetch_all_embeddings(self) -> List[Tuple[str, bytes]]:
        cur = self.connect().execute(
            "SELECT key, embedding FROM items WHERE embedding IS NOT NULL"
        )
        return [(row["key"], row["embedding"]) for row in cur]

    def save_collections(self, collections: dict) -> None:
        """保存分类信息到数据库（接受 Dict[str, ZoteroCollection]）"""
        conn = self.connect()
        # 确保表存在
        conn.execute("""
            CREATE TABLE IF NOT EXISTS zotero_collections (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                parent_key TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_collections_parent ON zotero_collections(parent_key)")

        # 清空旧数据并插入新数据
        conn.execute("DELETE FROM zotero_collections")
        for coll in collections.values():
            conn.execute(
                "INSERT INTO zotero_collections(key, name, parent_key) VALUES(?, ?, ?)",
                (coll.key, coll.name, coll.parent_key),
            )
        conn.commit()

    def load_collections(self) -> dict:
        """从数据库加载分类信息，返回 Dict[str, dict]"""
        conn = self.connect()
        try:
            cur = conn.execute("SELECT key, name, parent_key FROM zotero_collections")
            result = {}
            for row in cur:
                result[row["key"]] = {
                    "key": row["key"],
                    "name": row["name"],
                    "parent_key": row["parent_key"],
                }
            return result
        except sqlite3.OperationalError:
            # 表不存在
            return {}

    def iter_items_in_collections(self, collection_ids: List[str]) -> Iterable[ZoteroItem]:
        """迭代属于指定分类的所有条目"""
        for item in self.iter_items():
            for coll_id in item.collections:
                if coll_id in collection_ids:
                    yield item
                    break


def _row_to_item(row: sqlite3.Row) -> ZoteroItem:
    return ZoteroItem(
        key=row["key"],
        version=row["version"],
        title=row["title"],
        abstract=row["abstract"],
        creators=json.loads(row["creators"] or "[]"),
        tags=json.loads(row["tags"] or "[]"),
        collections=json.loads(row["collections"] or "[]"),
        year=row["year"],
        doi=row["doi"],
        url=row["url"],
        raw=json.loads(row["raw_json"]),
    )


__all__ = ["ProfileStorage"]
