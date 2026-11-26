"""Deterministic 2-Camera Nested-Zone Movement & Away-Timer State Machine.

Physical Topology Rules:
- Room 1 (Outer): Only room with external Main Entry/Exit (Camera 1).
- Room 2 (Inner): No external doors; Connecting Door monitored by Camera 2 inside Room 2.
- Away Timer starts ONLY on exiting Room 1 to OUTSIDE via Camera 1.
- Internal shifts (IN_ROOM_1 <-> IN_ROOM_2 via Camera 2) NEVER start the away timer.
- Away Timer stops on returning INWARD via Camera 1 (away_duration = T_return - T_exit).
- Fail-safe reconciliation handles missed Camera 2 or Camera 1 crossings cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import uuid

from shared.models import (
    ActivePersonStateRecord,
    CrossingDirection,
    CrossingEventPayload,
    MovementEventType,
    MovementLogRecord,
    VerificationStatus,
    ZoneState,
    format_iso_utc,
    parse_iso_utc,
)


@dataclass
class StateTransitionResult:
    """Result of applying a crossing event to a person's zone state."""

    new_state: ActivePersonStateRecord
    log_record: MovementLogRecord
    should_send_away_alert: bool = False
    alert_reason: Optional[str] = None


def apply_crossing_event(
    current_state: Optional[ActivePersonStateRecord],
    event: CrossingEventPayload,
    away_alert_threshold_seconds: float = 900.0,
    auto_match_threshold: float = 0.55,
) -> StateTransitionResult:
    """Apply a bidirectional crossing event to a person's state deterministically."""
    event_dt = parse_iso_utc(event.timestamp)
    event_iso = format_iso_utc(event_dt)
    log_id = str(uuid.uuid4())

    if current_state is None:
        prev_zone = ZoneState.OUTSIDE
        last_exit_time = None
        last_entry_time = None
        last_exit_log_id = None
        away_alert_sent = False
        display_name = event.display_name or (
            "Unknown Person" if event.person_id == "UNKNOWN_TRACK" else f"Student {event.person_id}"
        )
    else:
        prev_zone = current_state.current_zone
        last_exit_time = current_state.last_exit_time
        last_entry_time = current_state.last_entry_time
        last_exit_log_id = current_state.last_exit_log_id
        away_alert_sent = current_state.away_alert_sent
        display_name = event.display_name or current_state.display_name

    # Default verification status: UNKNOWN_TRACK or low-confidence matches require human review.
    is_unknown = event.person_id == "UNKNOWN_TRACK" or event.confidence < auto_match_threshold
    verification_status = (
        VerificationStatus.PENDING_REVIEW if is_unknown else VerificationStatus.AUTO_MATCHED
    )
    remarks: Optional[str] = (
        "Low ReID confidence or unknown identity; awaiting authority verification."
        if is_unknown
        else None
    )

    to_zone = prev_zone
    event_type = MovementEventType.ANOMALY_REVIEW
    away_duration_seconds: Optional[float] = None
    paired_exit_log_id: Optional[str] = None
    should_alert = False
    alert_reason: Optional[str] = None

    direction = event.direction

    if direction == CrossingDirection.CAM1_INWARD:
        to_zone = ZoneState.IN_ROOM_1
        if prev_zone == ZoneState.OUTSIDE:
            if last_exit_time:
                exit_dt = parse_iso_utc(last_exit_time)
                away_duration_seconds = round(max(0.0, (event_dt - exit_dt).total_seconds()), 2)
                event_type = MovementEventType.CAMPUS_RETURN
                paired_exit_log_id = last_exit_log_id
                if away_duration_seconds > away_alert_threshold_seconds:
                    should_alert = True
                    alert_reason = (
                        f"{display_name} ({event.person_id}) returned after "
                        f"{away_duration_seconds:.0f}s away (threshold {away_alert_threshold_seconds:.0f}s)."
                    )
            else:
                event_type = MovementEventType.CAMPUS_ENTRY
            last_exit_time = None
            last_exit_log_id = None
            last_entry_time = event_iso
            away_alert_sent = False
        else:
            # Already marked inside Room 1 or Room 2 -> missed previous exit
            event_type = MovementEventType.CAMPUS_ENTRY_RECONCILED
            verification_status = VerificationStatus.PENDING_REVIEW
            remarks = _append_remark(
                remarks,
                f"Auto-reconciled: Cam 1 inward crossing detected while state was {prev_zone.value}.",
            )
            last_exit_time = None
            last_exit_log_id = None
            last_entry_time = event_iso
            away_alert_sent = False

    elif direction == CrossingDirection.CAM2_INWARD:
        to_zone = ZoneState.IN_ROOM_2
        if prev_zone == ZoneState.IN_ROOM_1:
            event_type = MovementEventType.INTERNAL_SHIFT_R1_TO_R2
            # Internal shift NEVER starts away timer.
        elif prev_zone == ZoneState.OUTSIDE:
            # Fail-safe: Cam 1 missed entry into Room 1 before walking into Room 2.
            event_type = MovementEventType.CAMPUS_ENTRY_RECONCILED
            verification_status = VerificationStatus.PENDING_REVIEW
            remarks = _append_remark(
                remarks,
                "Auto-reconciled: Cam 2 entry into Room 2 without prior Cam 1 entry.",
            )
            if last_exit_time:
                exit_dt = parse_iso_utc(last_exit_time)
                away_duration_seconds = round(max(0.0, (event_dt - exit_dt).total_seconds()), 2)
                paired_exit_log_id = last_exit_log_id
            last_exit_time = None
            last_exit_log_id = None
            last_entry_time = event_iso
            away_alert_sent = False
        else:
            event_type = MovementEventType.ANOMALY_REVIEW
            verification_status = VerificationStatus.PENDING_REVIEW
            remarks = _append_remark(
                remarks,
                "Duplicate Cam 2 inward crossing while already IN_ROOM_2.",
            )

    elif direction == CrossingDirection.CAM2_OUTWARD:
        to_zone = ZoneState.IN_ROOM_1
        if prev_zone == ZoneState.IN_ROOM_2:
            event_type = MovementEventType.INTERNAL_SHIFT_R2_TO_R1
            # Internal shift NEVER starts away timer.
        else:
            event_type = MovementEventType.INTERNAL_SHIFT_R2_TO_R1
            verification_status = VerificationStatus.PENDING_REVIEW
            remarks = _append_remark(
                remarks,
                f"Auto-reconciled: Cam 2 outward crossing while state was {prev_zone.value}.",
            )
            if prev_zone == ZoneState.OUTSIDE and last_exit_time:
                last_exit_time = None
                last_exit_log_id = None
                last_entry_time = event_iso
                away_alert_sent = False

    elif direction == CrossingDirection.CAM1_OUTWARD:
        to_zone = ZoneState.OUTSIDE
        if prev_zone == ZoneState.IN_ROOM_1:
            event_type = MovementEventType.CAMPUS_EXIT
            last_exit_time = event_iso
            last_exit_log_id = log_id
            away_alert_sent = False
        elif prev_zone == ZoneState.IN_ROOM_2:
            # Fail-safe reconciliation: Cam 2 missed Room 2 -> Room 1 exit before Cam 1 exit.
            event_type = MovementEventType.CAMPUS_EXIT_RECONCILED
            verification_status = VerificationStatus.PENDING_REVIEW
            remarks = _append_remark(
                remarks,
                "Auto-reconciled: exited Room 1 via Cam 1 while still marked IN_ROOM_2 (missed Cam 2 exit).",
            )
            last_exit_time = event_iso
            last_exit_log_id = log_id
            away_alert_sent = False
        else:
            event_type = MovementEventType.ANOMALY_REVIEW
            verification_status = VerificationStatus.PENDING_REVIEW
            remarks = _append_remark(
                remarks,
                "Cam 1 outward crossing detected while already OUTSIDE.",
            )
            if not last_exit_time:
                last_exit_time = event_iso
                last_exit_log_id = log_id
                away_alert_sent = False

    new_state = ActivePersonStateRecord(
        person_id=event.person_id,
        display_name=display_name,
        current_zone=to_zone,
        last_exit_time=last_exit_time,
        last_entry_time=last_entry_time,
        last_exit_log_id=last_exit_log_id,
        last_camera_id=event.camera_id,
        away_alert_sent=away_alert_sent,
        updated_at=event_iso,
    )

    log_record = MovementLogRecord(
        log_id=log_id,
        event_id=event.event_id,
        gsi_pk="LOG",
        timestamp=event_iso,
        person_id=event.person_id,
        display_name=display_name,
        camera_id=event.camera_id,
        event_type=event_type,
        from_zone=prev_zone,
        to_zone=to_zone,
        away_duration_seconds=away_duration_seconds,
        snapshot_s3_key=event.snapshot_s3_key,
        confidence=round(float(event.confidence), 4),
        verification_status=verification_status,
        verified_by=None,
        remarks=remarks,
        paired_exit_log_id=paired_exit_log_id,
        audit_history=[],
    )

    return StateTransitionResult(
        new_state=new_state,
        log_record=log_record,
        should_send_away_alert=should_alert,
        alert_reason=alert_reason,
    )


def _append_remark(existing: Optional[str], addition: str) -> str:
    if not existing:
        return addition
    return f"{existing} | {addition}"
