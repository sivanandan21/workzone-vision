"""Unit tests for the 2-Camera Nested-Zone Movement & Away-Timer State Machine."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from away_watchdog.handler import check_overdue_outside_persons
from ingest_processor.handler import process_crossing_payload
from shared.models import (
    CrossingDirection,
    MovementEventType,
    VerificationStatus,
    ZoneState,
)
from tests.mock_aws_store import MockLocalStateStore


def _make_event(event_id: str, person_id: str, cam_id: str, direction: str, ts: str, conf: float = 0.95):
    return {
        "event_id": event_id,
        "person_id": person_id,
        "display_name": "Student 1" if person_id == "001" else None,
        "camera_id": cam_id,
        "direction": direction,
        "timestamp": ts,
        "confidence": conf,
    }


def test_full_nested_lifecycle_and_away_timer(tmp_path: Path) -> None:
    store = MockLocalStateStore(
        db_path=str(tmp_path / "state.db"),
        snapshots_dir=str(tmp_path / "s3"),
        away_alert_threshold_seconds=600.0,
    )
    try:
        # 1. Enter Room 1 via Cam 1
        r1 = store.ingest_edge_event(_make_event("e1", "001", "cam_1", CrossingDirection.CAM1_INWARD.value, "2026-11-26T09:00:00Z"))
        assert r1["to_zone"] == ZoneState.IN_ROOM_1.value
        assert r1["event_type"] == MovementEventType.CAMPUS_ENTRY.value
        assert r1["away_duration_seconds"] is None

        # 2. Shift Room 1 -> Room 2 via Cam 2 (MUST NOT start away timer)
        r2 = store.ingest_edge_event(_make_event("e2", "001", "cam_2", CrossingDirection.CAM2_INWARD.value, "2026-11-26T09:05:00Z"))
        assert r2["to_zone"] == ZoneState.IN_ROOM_2.value
        assert r2["event_type"] == MovementEventType.INTERNAL_SHIFT_R1_TO_R2.value
        assert store.get_person_state("001").last_exit_time is None

        # 3. Shift Room 2 -> Room 1 via Cam 2 (MUST NOT start away timer)
        r3 = store.ingest_edge_event(_make_event("e3", "001", "cam_2", CrossingDirection.CAM2_OUTWARD.value, "2026-11-26T09:20:00Z"))
        assert r3["to_zone"] == ZoneState.IN_ROOM_1.value
        assert r3["event_type"] == MovementEventType.INTERNAL_SHIFT_R2_TO_R1.value
        assert store.get_person_state("001").last_exit_time is None

        # 4. Exit Room 1 -> OUTSIDE via Cam 1 (STARTS Away Timer)
        r4 = store.ingest_edge_event(_make_event("e4", "001", "cam_1", CrossingDirection.CAM1_OUTWARD.value, "2026-11-26T10:00:00Z"))
        assert r4["to_zone"] == ZoneState.OUTSIDE.value
        assert r4["event_type"] == MovementEventType.CAMPUS_EXIT.value
        assert store.get_person_state("001").last_exit_time == "2026-11-26T10:00:00Z"

        # 5. Return OUTSIDE -> Room 1 after 15 minutes (900s > 600s threshold -> triggers SNS alert)
        r5 = store.ingest_edge_event(_make_event("e5", "001", "cam_1", CrossingDirection.CAM1_INWARD.value, "2026-11-26T10:15:00Z"))
        assert r5["to_zone"] == ZoneState.IN_ROOM_1.value
        assert r5["event_type"] == MovementEventType.CAMPUS_RETURN.value
        assert r5["away_duration_seconds"] == 900.0
        assert r5["alert_triggered"] is True
        assert len(store.sns_client.published_messages) == 1
        assert store.get_person_state("001").last_exit_time is None
    finally:
        store.close()


def test_failsafe_reconciliation_missed_cam2_exit(tmp_path: Path) -> None:
    store = MockLocalStateStore(db_path=str(tmp_path / "state.db"), snapshots_dir=str(tmp_path / "s3"))
    try:
        store.ingest_edge_event(_make_event("e1", "001", "cam_1", "CAM1_INWARD", "2026-11-26T09:00:00Z"))
        store.ingest_edge_event(_make_event("e2", "001", "cam_2", "CAM2_INWARD", "2026-11-26T09:05:00Z"))
        # Cam 2 misses exit, person walks out Cam 1 directly while still marked IN_ROOM_2
        res = store.ingest_edge_event(_make_event("e3", "001", "cam_1", "CAM1_OUTWARD", "2026-11-26T09:30:00Z"))
        assert res["from_zone"] == ZoneState.IN_ROOM_2.value
        assert res["to_zone"] == ZoneState.OUTSIDE.value
        assert res["event_type"] == MovementEventType.CAMPUS_EXIT_RECONCILED.value
        assert res["verification_status"] == VerificationStatus.PENDING_REVIEW.value
        assert store.get_person_state("001").last_exit_time == "2026-11-26T09:30:00Z"
    finally:
        store.close()


def test_idempotent_duplicate_event_id_and_watchdog_alert(tmp_path: Path) -> None:
    store = MockLocalStateStore(db_path=str(tmp_path / "state.db"), snapshots_dir=str(tmp_path / "s3"))
    try:
        store.ingest_edge_event(_make_event("e1", "001", "cam_1", "CAM1_INWARD", "2026-11-26T09:00:00Z"))
        store.ingest_edge_event(_make_event("e2", "001", "cam_1", "CAM1_OUTWARD", "2026-11-26T09:10:00Z"))
        # Replay exact duplicate e2
        dup = store.ingest_edge_event(_make_event("e2", "001", "cam_1", "CAM1_OUTWARD", "2026-11-26T09:10:00Z"))
        assert dup["status"] == "duplicate_ignored"
        assert len(store.list_movement_logs()) == 2

        # Watchdog scan at 09:30 (20 min > 15 min threshold) triggers once
        alerts_1 = check_overdue_outside_persons(
            repo=store,
            away_alert_threshold_seconds=900.0,
            now_utc=datetime(2026, 11, 26, 9, 30, 0, tzinfo=timezone.utc),
            sns_client=store.sns_client,
            sns_topic_arn="arn:aws:sns:local:000:alerts",
        )
        assert len(alerts_1) == 1
        alerts_2 = check_overdue_outside_persons(
            repo=store,
            away_alert_threshold_seconds=900.0,
            now_utc=datetime(2026, 11, 26, 9, 31, 0, tzinfo=timezone.utc),
            sns_client=store.sns_client,
            sns_topic_arn="arn:aws:sns:local:000:alerts",
        )
        assert len(alerts_2) == 0
    finally:
        store.close()
