"""Unit tests for PostgresStateRepository SQL queries, DDL, idempotency, and round-trip mapping."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from backend.postgres_repository import POSTGRES_DDL, PostgresStateRepository
from shared.models import (
    ActivePersonStateRecord,
    MovementEventType,
    MovementLogRecord,
    VerificationStatus,
    ZoneState,
)


class _FakeCursor:
    def __init__(self, state: Dict[str, Any]) -> None:
        self._state = state
        self._result: Optional[Any] = None
        self._results: List[Any] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._state["queries"].append((sql.strip(), params))
        norm = " ".join(sql.split()).upper()
        self._result = None
        self._results = []

        if norm == "SELECT 1":
            self._result = (1,)
        elif "INSERT INTO PROCESSED_EDGE_EVENTS" in norm:
            eid = params[0]
            if eid in self._state["events"]:
                self._result = None
            else:
                self._state["events"].add(eid)
                self._result = (eid,)
        elif "INSERT INTO ACTIVE_PERSON_STATE" in norm:
            self._state["persons"][params[0]] = params
        elif "FROM ACTIVE_PERSON_STATE WHERE PERSON_ID" in norm:
            self._result = self._state["persons"].get(params[0])
        elif "FROM ACTIVE_PERSON_STATE" in norm:
            self._results = list(self._state["persons"].values())
        elif "INSERT INTO MOVEMENT_LOGS" in norm:
            row = list(params)
            row[17] = json.loads(row[17])
            self._state["logs"][params[0]] = tuple(row)
        elif "FROM MOVEMENT_LOGS WHERE LOG_ID" in norm:
            self._result = self._state["logs"].get(params[0])
        elif "FROM MOVEMENT_LOGS WHERE PAIRED_EXIT_LOG_ID" in norm:
            for r in self._state["logs"].values():
                if r[16] == params[0]:
                    self._result = r
                    break
        elif "FROM MOVEMENT_LOGS" in norm:
            self._results = list(self._state["logs"].values())

    def fetchone(self) -> Optional[Any]:
        return self._result

    def fetchall(self) -> List[Any]:
        return self._results


class _FakeConnection:
    def __init__(self, state: Dict[str, Any]) -> None:
        self._state = state

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._state)

    def commit(self) -> None:
        self._state["commits"] += 1


def test_postgres_repository_crud_and_idempotency() -> None:
    db_state: Dict[str, Any] = {"queries": [], "events": set(), "persons": {}, "logs": {}, "commits": 0}
    repo = PostgresStateRepository(
        database_url="postgresql://workzone:test@localhost:5432/workzone_vision",
        connect_fn=lambda _: _FakeConnection(db_state),
        init_schema=True,
    )

    assert "CREATE TABLE IF NOT EXISTS active_person_state" in POSTGRES_DDL
    assert "CREATE TABLE IF NOT EXISTS movement_logs" in POSTGRES_DDL
    assert repo.ping() is True

    # Idempotency check
    assert repo.claim_event_id("evt-pg-1") is True
    assert repo.claim_event_id("evt-pg-1") is False

    # Person state upsert & fetch
    state = ActivePersonStateRecord(
        person_id="001",
        display_name="Student 1",
        current_zone=ZoneState.IN_ROOM_1,
        last_entry_time="2026-11-26T10:00:00Z",
        last_camera_id="cam_1",
        updated_at="2026-11-26T10:00:00Z",
    )
    repo.save_person_state(state)
    loaded = repo.get_person_state("001")
    assert loaded is not None
    assert loaded.current_zone == ZoneState.IN_ROOM_1
    assert len(repo.list_person_states()) == 1

    # Movement log & paired exit lookup with JSONB audit_history
    log = MovementLogRecord(
        log_id="log-pg-2",
        event_id="evt-pg-2",
        timestamp="2026-11-26T10:15:00Z",
        person_id="001",
        display_name="Student 1",
        camera_id="cam_1",
        event_type=MovementEventType.CAMPUS_RETURN,
        from_zone=ZoneState.OUTSIDE,
        to_zone=ZoneState.IN_ROOM_1,
        away_duration_seconds=900.0,
        confidence=0.96,
        verification_status=VerificationStatus.AUTO_MATCHED,
        paired_exit_log_id="log-pg-exit",
        audit_history=[{"action": "VERIFY", "actor": "warden", "timestamp": "2026-11-26T10:16:00Z"}],
    )
    repo.save_movement_log(log)
    fetched = repo.get_movement_log("log-pg-2")
    assert fetched is not None
    assert fetched.away_duration_seconds == 900.0
    assert fetched.audit_history[0]["action"] == "VERIFY"
    assert repo.find_paired_return_log("log-pg-exit").log_id == "log-pg-2"
