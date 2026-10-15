"""SQLite durable state. Outbox snapshots survive event changes and restarts."""
import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=20000;
            CREATE TABLE IF NOT EXISTS messages (
                key TEXT PRIMARY KEY, payload TEXT NOT NULL, received_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, title_key TEXT NOT NULL, group_name TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE,
                event_id TEXT NOT NULL, version INTEGER NOT NULL, reason TEXT NOT NULL,
                due_at TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT, claimed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS capture_seen (
                group_name TEXT NOT NULL, fingerprint TEXT NOT NULL,
                PRIMARY KEY (group_name, fingerprint)
            );
            CREATE TABLE IF NOT EXISTS capture_state (
                group_name TEXT PRIMARY KEY, last_success_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS forward_reads (
                group_name TEXT NOT NULL, parent_id TEXT NOT NULL,
                status TEXT NOT NULL, payload TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT, last_error TEXT,
                PRIMARY KEY (group_name, parent_id)
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def events(self) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM events ORDER BY rowid")]

    def jobs(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM outbox ORDER BY due_at,id")]

