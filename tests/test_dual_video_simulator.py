"""End-to-end integration test running dual-MP4 synthetic scenario through Edge + State Machine."""

from __future__ import annotations

from pathlib import Path

from shared.models import MovementEventType, ZoneState
from tests.simulator_cli import run_simulation


def test_dual_video_simulator_end_to_end(tmp_path: Path) -> None:
    result = run_simulation(
        synthetic=True,
        work_dir=tmp_path / "sim_e2e",
        strict_target_mode=True,
        away_alert_threshold_seconds=600.0,
    )

    assert result["pending_outbox"] == 0
    states = {s["person_id"]: s for s in result["states"]}
    assert "001" in states
    # Bystander in Room 1 must be ignored in strict 2-student mode
    assert set(states.keys()) == {"001"}
    assert states["001"]["current_zone"] == ZoneState.IN_ROOM_1.value

    # Logs ordered newest first; reverse for chronological order
    logs_chrono = list(reversed(result["logs"]))
    event_types = [log["event_type"] for log in logs_chrono]

    assert event_types == [
        MovementEventType.CAMPUS_ENTRY.value,
        MovementEventType.INTERNAL_SHIFT_R1_TO_R2.value,
        MovementEventType.INTERNAL_SHIFT_R2_TO_R1.value,
        MovementEventType.CAMPUS_EXIT.value,
        MovementEventType.CAMPUS_RETURN.value,
    ]

    # Internal Room 1 <-> Room 2 shifts NEVER trigger away duration
    assert logs_chrono[1]["away_duration_seconds"] is None
    assert logs_chrono[2]["away_duration_seconds"] is None

    # Return log records exact measured away duration and saved snapshot crop
    return_log = logs_chrono[4]
    assert return_log["away_duration_seconds"] is not None
    assert 2.0 <= return_log["away_duration_seconds"] <= 8.0
    assert return_log["snapshot_s3_key"]
    snap_name = Path(return_log["snapshot_s3_key"]).name
    assert (tmp_path / "sim_e2e" / "mock_s3" / snap_name).is_file()
