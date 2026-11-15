from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .detectors.base import EventDraft


class EventStore:
    """SQLite queue of detector events, with de-duplication cooldowns."""

    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, source TEXT, kind TEXT, severity TEXT,
                summary TEXT, details TEXT, dedupe_key TEXT, alerted INTEGER DEFAULT 0, handled INTEGER DEFAULT 0)"""
            )
            self.db.commit()

    def add(self, d: EventDraft, now: float | None = None) -> int | None:
        now = now if now is not None else time.time()
        with self.lock:
            if d.dedupe_key:
                row = self.db.execute(
                    "SELECT 1 FROM events WHERE dedupe_key=? AND ts>? LIMIT 1", (d.dedupe_key, now - d.cooldown)
                ).fetchone()
                if row:
                    return None
            cur = self.db.execute(
                "INSERT INTO events(ts,source,kind,severity,summary,details,dedupe_key) VALUES(?,?,?,?,?,?,?)",
                (now, d.source, d.kind, d.severity, d.summary, json.dumps(d.details), d.dedupe_key),
            )
            self.db.commit()
            return cur.lastrowid

    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d["details"] or "{}")
            out.append(d)
        return out

    def pending(self, limit: int = 50) -> list[dict]:
        return self._rows("SELECT * FROM events WHERE handled=0 ORDER BY id LIMIT ?", (limit,))

    def recent(self, hours: float = 24, limit: int = 30, exclude: list[int] | None = None) -> list[dict]:
        rows = self._rows(
            "SELECT * FROM events WHERE ts>? ORDER BY id DESC LIMIT ?", (time.time() - hours * 3600, limit + len(exclude or []))
        )
        return [r for r in rows if r["id"] not in set(exclude or [])][:limit]

    def _mark(self, col: str, ids: list[int]) -> None:
        if not ids:
            return
        with self.lock:
            self.db.execute(f"UPDATE events SET {col}=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
            self.db.commit()

    def mark_alerted(self, ids: list[int]) -> None:
        self._mark("alerted", ids)

    def mark_handled(self, ids: list[int]) -> None:
        self._mark("handled", ids)
