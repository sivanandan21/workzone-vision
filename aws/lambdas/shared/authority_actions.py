"""Authority verification, timestamp adjustment, audit trail, and CSV export services."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
from typing import Optional

from shared.models import (
    ActivePersonStateRecord,
    AuditEntry,
    MovementEventType,
    MovementLogRecord,
    VerificationStatus,
    ZoneState,
    format_iso_utc,
    parse_iso_utc,
)
from shared.repository import StateRepository


class AuthorizationError(PermissionError):
    """Raised when caller lacks Authorities group privileges."""


class LogNotFoundError(KeyError):
    """Raised when a log_id cannot be found."""


def assert_authority_role(groups: list) -> None:
    """Require Authorities role (or local dev admin) to mutate verification state."""
    normalized = {str(g).strip().lower() for g in (groups or [])}
    if "authorities" not in normalized and "admin" not in normalized:
        raise AuthorizationError("Action requires 'Authorities' Cognito group membership.")


def _now_iso() -> str:
    return format_iso_utc(datetime.now(timezone.utc))


def verify_movement_log(
    repo: StateRepository,
    log_id: str,
    actor: str,
    remarks: Optional[str] = None,
    assign_person_id: Optional[str] = None,
    assign_display_name: Optional[str] = None,
) -> MovementLogRecord:
    """Verify a movement log (and optionally assign identity for UNKNOWN_TRACK)."""
    log = repo.get_movement_log(log_id)
    if log is None:
        raise LogNotFoundError(f"Movement log '{log_id}' not found.")

    prev_values = {
        "verification_status": log.verification_status.value,
        "person_id": log.person_id,
        "display_name": log.display_name,
        "remarks": log.remarks,
    }

    if assign_person_id and assign_person_id.strip():
        new_pid = assign_person_id.strip()
        log.person_id = new_pid
        log.display_name = assign_display_name or f"Student {new_pid}"
        # Keep ActivePersonState synced if no newer event supersedes it
        existing_state = repo.get_person_state(new_pid)
        if existing_state is None or parse_iso_utc(existing_state.updated_at) <= parse_iso_utc(log.timestamp):
            is_exit = log.to_zone == ZoneState.OUTSIDE
            repo.save_person_state(
                ActivePersonStateRecord(
                    person_id=new_pid,
                    display_name=log.display_name,
                    current_zone=log.to_zone,
                    last_exit_time=log.timestamp if is_exit else None,
                    last_entry_time=log.timestamp if not is_exit else (existing_state.last_entry_time if existing_state else None),
                    last_exit_log_id=log.log_id if is_exit else None,
                    last_camera_id=log.camera_id,
                    away_alert_sent=False,
                    updated_at=log.timestamp,
                )
            )

    log.verification_status = VerificationStatus.VERIFIED
    log.verified_by = actor
    if remarks is not None:
        log.remarks = remarks

    audit = AuditEntry(
        action="VERIFY" if not assign_person_id else "VERIFY_AND_ASSIGN",
        actor=actor,
        timestamp=_now_iso(),
        remarks=remarks,
        previous_values=prev_values,
        new_values={
            "verification_status": log.verification_status.value,
            "person_id": log.person_id,
            "display_name": log.display_name,
            "remarks": log.remarks,
        },
    )
    log.audit_history.append(audit.model_dump())
    repo.save_movement_log(log)
    return log


def flag_movement_log(
    repo: StateRepository,
    log_id: str,
    actor: str,
    remarks: str,
) -> MovementLogRecord:
    """Flag a movement log as disputed/anomalous with mandatory remarks."""
    if not remarks or not remarks.strip():
        raise ValueError("Remarks are required when flagging a movement log.")

    log = repo.get_movement_log(log_id)
    if log is None:
        raise LogNotFoundError(f"Movement log '{log_id}' not found.")

    prev_values = {
        "verification_status": log.verification_status.value,
        "remarks": log.remarks,
    }
    log.verification_status = VerificationStatus.FLAGGED
    log.verified_by = actor
    log.remarks = remarks.strip()

    audit = AuditEntry(
        action="FLAG",
        actor=actor,
        timestamp=_now_iso(),
        remarks=log.remarks,
        previous_values=prev_values,
        new_values={
            "verification_status": log.verification_status.value,
            "remarks": log.remarks,
        },
    )
    log.audit_history.append(audit.model_dump())
    repo.save_movement_log(log)
    return log


def adjust_log_timestamp(
    repo: StateRepository,
    log_id: str,
    new_timestamp: str,
    actor: str,
    remarks: str,
) -> MovementLogRecord:
    """Adjust crossing timestamp, record audit trail, and recompute away durations."""
    if not remarks or not remarks.strip():
        raise ValueError("Remarks are required when adjusting a timestamp.")

    log = repo.get_movement_log(log_id)
    if log is None:
        raise LogNotFoundError(f"Movement log '{log_id}' not found.")

    new_dt = parse_iso_utc(new_timestamp)
    new_iso = format_iso_utc(new_dt)
    prev_values = {
        "timestamp": log.timestamp,
        "away_duration_seconds": log.away_duration_seconds,
        "verification_status": log.verification_status.value,
        "remarks": log.remarks,
    }

    log.timestamp = new_iso
    log.verification_status = VerificationStatus.VERIFIED
    log.verified_by = actor
    log.remarks = remarks.strip()

    # Recompute away_duration_seconds if this is a return log paired with an exit log
    if log.paired_exit_log_id:
        exit_log = repo.get_movement_log(log.paired_exit_log_id)
        if exit_log is not None:
            exit_dt = parse_iso_utc(exit_log.timestamp)
            log.away_duration_seconds = round(max(0.0, (new_dt - exit_dt).total_seconds()), 2)

    # Or if this is an exit log paired with a subsequent return log, update that return log too
    if log.event_type in (MovementEventType.CAMPUS_EXIT, MovementEventType.CAMPUS_EXIT_RECONCILED):
        paired_return = repo.find_paired_return_log(log.log_id)
        if paired_return is not None:
            ret_dt = parse_iso_utc(paired_return.timestamp)
            paired_return.away_duration_seconds = round(max(0.0, (ret_dt - new_dt).total_seconds()), 2)
            paired_return.audit_history.append(
                AuditEntry(
                    action="RECALCULATE_FROM_EXIT_ADJUST",
                    actor=actor,
                    timestamp=_now_iso(),
                    remarks=f"Recalculated after exit log {log.log_id} adjusted to {new_iso}",
                    new_values={"away_duration_seconds": paired_return.away_duration_seconds},
                ).model_dump()
            )
            repo.save_movement_log(paired_return)

        state = repo.get_person_state(log.person_id)
        if state is not None and state.last_exit_log_id == log.log_id:
            state.last_exit_time = new_iso
            state.updated_at = _now_iso()
            repo.save_person_state(state)

    audit = AuditEntry(
        action="ADJUST_TIMESTAMP",
        actor=actor,
        timestamp=_now_iso(),
        remarks=log.remarks,
        previous_values=prev_values,
        new_values={
            "timestamp": log.timestamp,
            "away_duration_seconds": log.away_duration_seconds,
            "verification_status": log.verification_status.value,
            "remarks": log.remarks,
        },
    )
    log.audit_history.append(audit.model_dump())
    repo.save_movement_log(log)
    return log


def export_logs_to_csv(logs: list) -> str:
    """Serialize MovementLogRecord objects into an RFC-4180 CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "log_id",
        "timestamp",
        "person_id",
        "display_name",
        "camera_id",
        "event_type",
        "from_zone",
        "to_zone",
        "away_duration_seconds",
        "confidence",
        "verification_status",
        "verified_by",
        "remarks",
        "snapshot_s3_key",
    ])
    for log in logs:
        writer.writerow([
            log.log_id,
            log.timestamp,
            log.person_id,
            log.display_name,
            log.camera_id,
            log.event_type.value,
            log.from_zone.value,
            log.to_zone.value,
            "" if log.away_duration_seconds is None else f"{log.away_duration_seconds:.2f}",
            f"{log.confidence:.4f}",
            log.verification_status.value,
            log.verified_by or "",
            log.remarks or "",
            log.snapshot_s3_key or "",
        ])
    return output.getvalue()
