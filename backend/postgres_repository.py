"""PostgreSQL implementation of StateRepository for AWS EC2 Docker deployment."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LAMBDAS_DIR = ROOT / "aws" / "lambdas"
if str(LAMBDAS_DIR) not in sys.path:
    sys.path.insert(0, str(LAMBDAS_DIR))

from shared.models import (
    ActivePersonStateRecord,
    MovementEventType,
    MovementLogRecord,
    VerificationStatus,
    ZoneState,
)
from shared.repository import StateRepository

POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS active_person_state (
    person_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    current_zone TEXT NOT NULL,
    last_exit_time TEXT NULL,
    last_entry_time TEXT NULL,
    last_exit_log_id TEXT NULL,
    last_camera_id TEXT NULL,
    away_alert_sent BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movement_logs (
    log_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    gsi_pk TEXT NOT NULL DEFAULT 'LOG',
    timestamp TEXT NOT NULL,
    person_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_zone TEXT NOT NULL,
    to_zone TEXT NOT NULL,
    away_duration_seconds DOUBLE PRECISION NULL,
    snapshot_s3_key TEXT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    verification_status TEXT NOT NULL,
    verified_by TEXT NULL,
    remarks TEXT NULL,
    paired_exit_log_id TEXT NULL,
    audit_history JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_movement_logs_ts ON movement_logs (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_movement_logs_person_ts ON movement_logs (person_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_movement_logs_paired_exit ON movement_logs (paired_exit_log_id);

CREATE TABLE IF NOT EXISTS processed_edge_events (
    event_id TEXT PRIMARY KEY,
    expires_at DOUBLE PRECISION NOT NULL
);
"""


class PostgresStateRepository(StateRepository):
    """PostgreSQL implementation matching DynamoDBStateRepository & MockLocalStateStore."""

    def __init__(
        self,
        database_url: str,
        connect_fn: Optional[Callable[..., Any]] = None,
        init_schema: bool = True,
    ) -> None:
        self.database_url = database_url
        if connect_fn is not None:
            self._connect_fn = connect_fn
        else:
            import psycopg
            self._connect_fn = psycopg.connect

        if init_schema:
            self.ensure_schema()

    def _connect(self) -> Any:
        return self._connect_fn(self.database_url)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                for stmt in [s.strip() for s in POSTGRES_DDL.split(";") if s.strip()]:
                    cur.execute(stmt)
            conn.commit()

    def ping(self) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
                return bool(row and row[0] == 1)

    def claim_event_id(self, event_id: str, ttl_seconds: int = 86400 * 7) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO processed_edge_events (event_id, expires_at)
                    VALUES (%s, %s)
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    (event_id, time.time() + float(ttl_seconds)),
                )
                row = cur.fetchone()
            conn.commit()
            return row is not None

    def get_person_state(self, person_id: str) -> Optional[ActivePersonStateRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT person_id, display_name, current_zone, last_exit_time, last_entry_time,
                           last_exit_log_id, last_camera_id, away_alert_sent, updated_at
                    FROM active_person_state
                    WHERE person_id = %s
                    """,
                    (person_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return _row_to_person_state(row)

    def save_person_state(self, state: ActivePersonStateRecord) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO active_person_state (
                        person_id, display_name, current_zone, last_exit_time, last_entry_time,
                        last_exit_log_id, last_camera_id, away_alert_sent, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (person_id) DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        current_zone = EXCLUDED.current_zone,
                        last_exit_time = EXCLUDED.last_exit_time,
                        last_entry_time = EXCLUDED.last_entry_time,
                        last_exit_log_id = EXCLUDED.last_exit_log_id,
                        last_camera_id = EXCLUDED.last_camera_id,
                        away_alert_sent = EXCLUDED.away_alert_sent,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        state.person_id,
                        state.display_name,
                        state.current_zone.value,
                        state.last_exit_time,
                        state.last_entry_time,
                        state.last_exit_log_id,
                        state.last_camera_id,
                        state.away_alert_sent,
                        state.updated_at,
                    ),
                )
            conn.commit()

    def list_person_states(self) -> List[ActivePersonStateRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT person_id, display_name, current_zone, last_exit_time, last_entry_time,
                           last_exit_log_id, last_camera_id, away_alert_sent, updated_at
                    FROM active_person_state
                    ORDER BY person_id ASC
                    """
                )
                rows = cur.fetchall()
        return [_row_to_person_state(r) for r in rows]

    def save_movement_log(self, log: MovementLogRecord) -> None:
        audit_json = json.dumps(log.audit_history)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO movement_logs (
                        log_id, event_id, gsi_pk, timestamp, person_id, display_name, camera_id,
                        event_type, from_zone, to_zone, away_duration_seconds, snapshot_s3_key,
                        confidence, verification_status, verified_by, remarks, paired_exit_log_id,
                        audit_history
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s::jsonb
                    )
                    ON CONFLICT (log_id) DO UPDATE SET
                        timestamp = EXCLUDED.timestamp,
                        person_id = EXCLUDED.person_id,
                        display_name = EXCLUDED.display_name,
                        away_duration_seconds = EXCLUDED.away_duration_seconds,
                        verification_status = EXCLUDED.verification_status,
                        verified_by = EXCLUDED.verified_by,
                        remarks = EXCLUDED.remarks,
                        audit_history = EXCLUDED.audit_history
                    """,
                    (
                        log.log_id,
                        log.event_id,
                        log.gsi_pk,
                        log.timestamp,
                        log.person_id,
                        log.display_name,
                        log.camera_id,
                        log.event_type.value,
                        log.from_zone.value,
                        log.to_zone.value,
                        log.away_duration_seconds,
                        log.snapshot_s3_key,
                        log.confidence,
                        log.verification_status.value,
                        log.verified_by,
                        log.remarks,
                        log.paired_exit_log_id,
                        audit_json,
                    ),
                )
            conn.commit()

    def get_movement_log(self, log_id: str) -> Optional[MovementLogRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT log_id, event_id, gsi_pk, timestamp, person_id, display_name, camera_id,
                           event_type, from_zone, to_zone, away_duration_seconds, snapshot_s3_key,
                           confidence, verification_status, verified_by, remarks, paired_exit_log_id,
                           audit_history
                    FROM movement_logs
                    WHERE log_id = %s
                    """,
                    (log_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return _row_to_movement_log(row)

    def list_movement_logs(
        self,
        person_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[MovementLogRecord]:
        clauses = []
        params: List[Any] = []
        if person_id:
            clauses.append("person_id = %s")
            params.append(person_id)
        if verification_status:
            clauses.append("verification_status = %s")
            params.append(verification_status)
        if event_type:
            clauses.append("event_type = %s")
            params.append(event_type)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT log_id, event_id, gsi_pk, timestamp, person_id, display_name, camera_id,
                           event_type, from_zone, to_zone, away_duration_seconds, snapshot_s3_key,
                           confidence, verification_status, verified_by, remarks, paired_exit_log_id,
                           audit_history
                    FROM movement_logs
                    {where_sql}
                    ORDER BY timestamp DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
        return [_row_to_movement_log(r) for r in rows]

    def find_paired_return_log(self, exit_log_id: str) -> Optional[MovementLogRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT log_id, event_id, gsi_pk, timestamp, person_id, display_name, camera_id,
                           event_type, from_zone, to_zone, away_duration_seconds, snapshot_s3_key,
                           confidence, verification_status, verified_by, remarks, paired_exit_log_id,
                           audit_history
                    FROM movement_logs
                    WHERE paired_exit_log_id = %s
                    LIMIT 1
                    """,
                    (exit_log_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return _row_to_movement_log(row)


def _row_to_person_state(row: tuple) -> ActivePersonStateRecord:
    return ActivePersonStateRecord(
        person_id=row[0],
        display_name=row[1],
        current_zone=ZoneState(row[2]),
        last_exit_time=row[3],
        last_entry_time=row[4],
        last_exit_log_id=row[5],
        last_camera_id=row[6],
        away_alert_sent=bool(row[7]),
        updated_at=row[8],
    )


def _row_to_movement_log(row: tuple) -> MovementLogRecord:
    raw_audit = row[17]
    if isinstance(raw_audit, str):
        audit_history = json.loads(raw_audit)
    elif isinstance(raw_audit, list):
        audit_history = raw_audit
    else:
        audit_history = []

    return MovementLogRecord(
        log_id=row[0],
        event_id=row[1],
        gsi_pk=row[2],
        timestamp=row[3],
        person_id=row[4],
        display_name=row[5],
        camera_id=row[6],
        event_type=MovementEventType(row[7]),
        from_zone=ZoneState(row[8]),
        to_zone=ZoneState(row[9]),
        away_duration_seconds=float(row[10]) if row[10] is not None else None,
        snapshot_s3_key=row[11],
        confidence=float(row[12]),
        verification_status=VerificationStatus(row[13]),
        verified_by=row[14],
        remarks=row[15],
        paired_exit_log_id=row[16],
        audit_history=audit_history,
    )
