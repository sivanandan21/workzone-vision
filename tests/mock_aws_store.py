"""Local SQLite mock implementation of AWS DynamoDB StateRepository, S3, and SNS."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LAMBDAS_DIR = ROOT / "aws" / "lambdas"
if str(LAMBDAS_DIR) not in sys.path:
    sys.path.insert(0, str(LAMBDAS_DIR))

from ingest_processor.handler import process_crossing_payload
from shared.models import ActivePersonStateRecord, MovementLogRecord
from shared.repository import StateRepository


class _MockSNSClient:
    def __init__(self) -> None:
        self.published_messages: List[Dict[str, Any]] = []

    def publish(self, TopicArn: str, Subject: str, Message: str) -> Dict[str, Any]:
        self.published_messages.append(
            {"topic_arn": TopicArn, "subject": Subject, "message": json.loads(Message)}
        )
        return {"MessageId": f"mock-sns-{len(self.published_messages)}"}


class MockLocalStateStore(StateRepository):
    """Drop-in local SQLite store matching DynamoDBStateRepository + local snapshot storage."""

    def __init__(
        self,
        db_path: str = "edge/data/local_state.db",
        snapshots_dir: str = "edge/data/mock_s3",
        away_alert_threshold_seconds: float = 900.0,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = Path(snapshots_dir)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.away_alert_threshold_seconds = away_alert_threshold_seconds
        self.sns_client = _MockSNSClient()

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_person_state (
                person_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS movement_logs (
                log_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                person_id TEXT NOT NULL,
                paired_exit_log_id TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id TEXT PRIMARY KEY,
                expires_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def claim_event_id(self, event_id: str, ttl_seconds: int = 86400 * 7) -> bool:
        try:
            self._conn.execute(
                "INSERT INTO processed_events (event_id, expires_at) VALUES (?, ?)",
                (event_id, time.time() + ttl_seconds),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_person_state(self, person_id: str) -> Optional[ActivePersonStateRecord]:
        row = self._conn.execute(
            "SELECT payload_json FROM active_person_state WHERE person_id = ?",
            (person_id,),
        ).fetchone()
        if not row:
            return None
        return ActivePersonStateRecord(**json.loads(row[0]))

    def save_person_state(self, state: ActivePersonStateRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO active_person_state (person_id, payload_json) VALUES (?, ?)
            ON CONFLICT(person_id) DO UPDATE SET payload_json = excluded.payload_json
            """,
            (state.person_id, state.model_dump_json()),
        )
        self._conn.commit()

    def list_person_states(self) -> List[ActivePersonStateRecord]:
        rows = self._conn.execute("SELECT payload_json FROM active_person_state").fetchall()
        return [ActivePersonStateRecord(**json.loads(r[0])) for r in rows]

    def save_movement_log(self, log: MovementLogRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO movement_logs (log_id, timestamp, person_id, paired_exit_log_id, payload_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(log_id) DO UPDATE SET
                timestamp = excluded.timestamp,
                person_id = excluded.person_id,
                paired_exit_log_id = excluded.paired_exit_log_id,
                payload_json = excluded.payload_json
            """,
            (
                log.log_id,
                log.timestamp,
                log.person_id,
                log.paired_exit_log_id,
                log.model_dump_json(),
            ),
        )
        self._conn.commit()

    def get_movement_log(self, log_id: str) -> Optional[MovementLogRecord]:
        row = self._conn.execute(
            "SELECT payload_json FROM movement_logs WHERE log_id = ?",
            (log_id,),
        ).fetchone()
        if not row:
            return None
        return MovementLogRecord(**json.loads(row[0]))

    def list_movement_logs(
        self,
        person_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[MovementLogRecord]:
        rows = self._conn.execute(
            "SELECT payload_json FROM movement_logs ORDER BY timestamp DESC LIMIT ?",
            (max(limit, 500),),
        ).fetchall()
        records = [MovementLogRecord(**json.loads(r[0])) for r in rows]
        if person_id:
            records = [r for r in records if r.person_id == person_id]
        if verification_status:
            records = [r for r in records if r.verification_status.value == verification_status]
        if event_type:
            records = [r for r in records if r.event_type.value == event_type]
        return records[:limit]

    def find_paired_return_log(self, exit_log_id: str) -> Optional[MovementLogRecord]:
        row = self._conn.execute(
            "SELECT payload_json FROM movement_logs WHERE paired_exit_log_id = ? LIMIT 1",
            (exit_log_id,),
        ).fetchone()
        if not row:
            return None
        return MovementLogRecord(**json.loads(row[0]))

    def ingest_edge_event(
        self,
        payload: Dict[str, Any],
        snapshot_local_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Simulates S3 snapshot upload + Lambda ingest_processor invocation."""
        if snapshot_local_path and Path(snapshot_local_path).is_file():
            s3_key = payload.get("snapshot_s3_key") or f"snapshots/{Path(snapshot_local_path).name}"
            dest = self.snapshots_dir / Path(s3_key).name
            if Path(snapshot_local_path).resolve() != dest.resolve():
                shutil.copy2(snapshot_local_path, dest)
            payload = dict(payload)
            payload["snapshot_s3_key"] = f"snapshots/{dest.name}"

        return process_crossing_payload(
            payload_dict=payload,
            repo=self,
            away_alert_threshold_seconds=self.away_alert_threshold_seconds,
            sns_client=self.sns_client,
            sns_topic_arn="arn:aws:sns:local:000000000000:mock-away-alerts",
        )

    def close(self) -> None:
        self._conn.close()
