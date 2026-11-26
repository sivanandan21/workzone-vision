"""Local SQLite WAL outbox queue for resilient offline-first cloud event delivery."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional


@dataclass
class QueuedEvent:
    event_id: str
    payload: Dict[str, Any]
    snapshot_local_path: Optional[str]
    retry_count: int
    created_at: float


class OfflineEventQueue:
    """SQLite-backed outbox storing crossing events when cloud connectivity drops."""

    def __init__(self, db_path: str = "edge/data/outbox.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_outbox (
                event_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                snapshot_local_path TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def enqueue(self, event_id: str, payload: Dict[str, Any], snapshot_local_path: Optional[str] = None) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO event_outbox (
                event_id, payload_json, snapshot_local_path, status, retry_count, created_at, updated_at
            ) VALUES (?, ?, ?, 'PENDING', 0, ?, ?)
            """,
            (event_id, json.dumps(payload), snapshot_local_path, now, now),
        )
        self._conn.commit()

    def peek_pending(self, limit: int = 25) -> List[QueuedEvent]:
        rows = self._conn.execute(
            """
            SELECT event_id, payload_json, snapshot_local_path, retry_count, created_at
            FROM event_outbox
            WHERE status = 'PENDING'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            QueuedEvent(
                event_id=r[0],
                payload=json.loads(r[1]),
                snapshot_local_path=r[2],
                retry_count=int(r[3]),
                created_at=float(r[4]),
            )
            for r in rows
        ]

    def mark_sent(self, event_id: str) -> None:
        self._conn.execute(
            "UPDATE event_outbox SET status = 'SENT', updated_at = ? WHERE event_id = ?",
            (time.time(), event_id),
        )
        self._conn.commit()

    def mark_failed(self, event_id: str, error_msg: str) -> None:
        self._conn.execute(
            """
            UPDATE event_outbox
            SET retry_count = retry_count + 1, last_error = ?, updated_at = ?
            WHERE event_id = ?
            """,
            (error_msg[:500], time.time(), event_id),
        )
        self._conn.commit()

    def pending_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM event_outbox WHERE status = 'PENDING'").fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        self._conn.close()
