"""Domain models and enumerations for WorkZone Vision nested-zone tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ZoneState(str, Enum):
    OUTSIDE = "OUTSIDE"
    IN_ROOM_1 = "IN_ROOM_1"
    IN_ROOM_2 = "IN_ROOM_2"


class CrossingDirection(str, Enum):
    CAM1_INWARD = "CAM1_INWARD"     # OUTSIDE -> IN_ROOM_1
    CAM1_OUTWARD = "CAM1_OUTWARD"   # IN_ROOM_1 -> OUTSIDE
    CAM2_INWARD = "CAM2_INWARD"     # IN_ROOM_1 -> IN_ROOM_2
    CAM2_OUTWARD = "CAM2_OUTWARD"   # IN_ROOM_2 -> IN_ROOM_1


class MovementEventType(str, Enum):
    CAMPUS_ENTRY = "CAMPUS_ENTRY"
    CAMPUS_RETURN = "CAMPUS_RETURN"
    INTERNAL_SHIFT_R1_TO_R2 = "INTERNAL_SHIFT_R1_TO_R2"
    INTERNAL_SHIFT_R2_TO_R1 = "INTERNAL_SHIFT_R2_TO_R1"
    CAMPUS_EXIT = "CAMPUS_EXIT"
    CAMPUS_EXIT_RECONCILED = "CAMPUS_EXIT_RECONCILED"
    CAMPUS_ENTRY_RECONCILED = "CAMPUS_ENTRY_RECONCILED"
    ANOMALY_REVIEW = "ANOMALY_REVIEW"


class VerificationStatus(str, Enum):
    AUTO_MATCHED = "AUTO_MATCHED"
    PENDING_REVIEW = "PENDING_REVIEW"
    VERIFIED = "VERIFIED"
    FLAGGED = "FLAGGED"


def parse_iso_utc(ts_str: str) -> datetime:
    """Parse an ISO-8601 timestamp string into an aware UTC datetime."""
    cleaned = ts_str.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_iso_utc(dt: datetime) -> str:
    """Format an aware datetime into canonical UTC ISO-8601 string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class CrossingEventPayload(BaseModel):
    """Crossing payload emitted by edge/ when a LineZone tripwire triggers."""

    event_id: str
    person_id: str
    display_name: Optional[str] = None
    camera_id: str
    direction: CrossingDirection
    timestamp: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    local_track_id: Optional[int] = None
    snapshot_s3_key: Optional[str] = None
    blur_score: Optional[float] = None
    reid_method: str = "hybrid"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ActivePersonStateRecord(BaseModel):
    """Represents a row in DynamoDB ActivePersonState."""

    person_id: str
    display_name: str
    current_zone: ZoneState = ZoneState.OUTSIDE
    last_exit_time: Optional[str] = None
    last_entry_time: Optional[str] = None
    last_exit_log_id: Optional[str] = None
    last_camera_id: Optional[str] = None
    away_alert_sent: bool = False
    updated_at: str


class AuditEntry(BaseModel):
    """Immutable audit record attached to MovementLogs on Authority changes."""

    action: str
    actor: str
    timestamp: str
    remarks: Optional[str] = None
    previous_values: Dict[str, Any] = Field(default_factory=dict)
    new_values: Dict[str, Any] = Field(default_factory=dict)


class MovementLogRecord(BaseModel):
    """Represents a row in DynamoDB MovementLogs."""

    log_id: str
    event_id: str
    gsi_pk: str = "LOG"
    timestamp: str
    person_id: str
    display_name: str
    camera_id: str
    event_type: MovementEventType
    from_zone: ZoneState
    to_zone: ZoneState
    away_duration_seconds: Optional[float] = None
    snapshot_s3_key: Optional[str] = None
    confidence: float = 1.0
    verification_status: VerificationStatus = VerificationStatus.AUTO_MATCHED
    verified_by: Optional[str] = None
    remarks: Optional[str] = None
    paired_exit_log_id: Optional[str] = None
    audit_history: List[Dict[str, Any]] = Field(default_factory=list)
