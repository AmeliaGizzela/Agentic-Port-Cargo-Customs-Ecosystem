"""Helper SQLite sederhana yang thread-safe. Setiap perusahaan memiliki file database sendiri."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")

    def script(self, sql: str) -> None:
        with self.lock:
            self.conn.executescript(sql)
            self.conn.commit()

    def execute(self, sql: str, params: tuple | list | dict = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur.lastrowid

    def executemany(self, sql: str, rows: list) -> None:
        with self.lock:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def query(self, sql: str, params: tuple | list | dict = ()) -> list[dict]:
        with self.lock:
            cur = self.conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def one(self, sql: str, params: tuple | list | dict = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: tuple | list | dict = ()):
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return row[0] if row else None

    def drop_all(self) -> None:
        with self.lock:
            tables = [r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for t in tables:
                self.conn.execute(f'DROP TABLE IF EXISTS "{t}"')
            self.conn.commit()


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(s: str | None, default=None):
    if s is None or s == "":
        return default
    return json.loads(s)
