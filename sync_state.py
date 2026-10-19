"""Small durable checkpoints for bridge delivery retries."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class SyncState:
    def __init__(self, path: Path | None = None):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path) if path else ":memory:")
        self.db.execute("CREATE TABLE IF NOT EXISTS receipt (id TEXT PRIMARY KEY, value TEXT, ts REAL)")
        self.db.execute("DELETE FROM receipt WHERE ts < ?", (time.time() - 30 * 86400,))
        self.db.commit()

    def get(self, key: str):
        row = self.db.execute("SELECT value FROM receipt WHERE id = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO receipt VALUES (?, ?, ?)",
                        (key, json.dumps(value, ensure_ascii=False), time.time()))
        self.db.commit()

    def close(self) -> None:
        self.db.close()
