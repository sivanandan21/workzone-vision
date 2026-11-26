"""Unit tests for duration filtering and user-friendly PDF / Excel report generation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import zipfile

from backend.report_generator import (
    build_report_data,
    filter_logs_by_window,
    format_human_duration,
    generate_excel_report,
    generate_pdf_report,
    resolve_duration_window,
)
from shared.models import (
    ActivePersonStateRecord,
    MovementEventType,
    MovementLogRecord,
    VerificationStatus,
    ZoneState,
)


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sample_records(now: datetime):
    states = [
        ActivePersonStateRecord(
            person_id="S-001",
            display_name="Aarav",
            current_zone=ZoneState.IN_ROOM_1,
            updated_at=_ts(now - timedelta(hours=1)),
        ),
        ActivePersonStateRecord(
            person_id="S-002",
            display_name="Diya",
            current_zone=ZoneState.IN_ROOM_2,
            updated_at=_ts(now - timedelta(days=3)),
        ),
    ]
    logs = [
        MovementLogRecord(
            log_id="log-1",
            event_id="evt-1",
            timestamp=_ts(now - timedelta(hours=1)),
            person_id="S-001",
            display_name="Aarav",
            camera_id="cam_1",
            event_type=MovementEventType.CAMPUS_RETURN,
            from_zone=ZoneState.OUTSIDE,
            to_zone=ZoneState.IN_ROOM_1,
            away_duration_seconds=325.0,
            verification_status=VerificationStatus.VERIFIED,
            remarks="Returned via main gate & verified <ok>",
        ),
        MovementLogRecord(
            log_id="log-2",
            event_id="evt-2",
            timestamp=_ts(now - timedelta(hours=12)),
            person_id="S-001",
            display_name="Aarav",
            camera_id="cam_1",
            event_type=MovementEventType.CAMPUS_EXIT,
            from_zone=ZoneState.IN_ROOM_1,
            to_zone=ZoneState.OUTSIDE,
            verification_status=VerificationStatus.AUTO_MATCHED,
        ),
        MovementLogRecord(
            log_id="log-3",
            event_id="evt-3",
            timestamp=_ts(now - timedelta(days=3)),
            person_id="S-002",
            display_name="Diya",
            camera_id="cam_2",
            event_type=MovementEventType.INTERNAL_SHIFT_R1_TO_R2,
            from_zone=ZoneState.IN_ROOM_1,
            to_zone=ZoneState.IN_ROOM_2,
            verification_status=VerificationStatus.PENDING_REVIEW,
        ),
        MovementLogRecord(
            log_id="log-4",
            event_id="evt-4",
            timestamp=_ts(now - timedelta(days=15)),
            person_id="S-002",
            display_name="Diya",
            camera_id="cam_1",
            event_type=MovementEventType.CAMPUS_ENTRY,
            from_zone=ZoneState.OUTSIDE,
            to_zone=ZoneState.IN_ROOM_1,
        ),
        MovementLogRecord(
            log_id="log-5",
            event_id="evt-5",
            timestamp=_ts(now - timedelta(days=45)),
            person_id="S-001",
            display_name="Aarav",
            camera_id="cam_1",
            event_type=MovementEventType.CAMPUS_EXIT,
            from_zone=ZoneState.IN_ROOM_1,
            to_zone=ZoneState.OUTSIDE,
            verification_status=VerificationStatus.FLAGGED,
        ),
    ]
    return logs, states


def test_format_human_duration() -> None:
    assert format_human_duration(None) == "—"
    assert format_human_duration(45) == "45s"
    assert format_human_duration(325.0) == "5m 25s"
    assert format_human_duration(3665.0) == "1h 01m 05s"


def test_duration_windows_and_filtering() -> None:
    # 18:30 IST on 2026-11-26 == 13:00 UTC on 2026-11-26
    now = datetime(2026, 11, 26, 13, 0, 0, tzinfo=timezone.utc)
    logs, _ = _sample_records(now)

    s_today, e_today, lbl_today = resolve_duration_window(
        "today", tz_offset_minutes=330, now_utc=now
    )
    assert "Today" in lbl_today
    assert len(filter_logs_by_window(logs, s_today, e_today)) == 2

    s_24h, e_24h, lbl_24h = resolve_duration_window("last_24h", now_utc=now)
    assert lbl_24h == "Last 24 Hours"
    assert len(filter_logs_by_window(logs, s_24h, e_24h)) == 2

    s_7d, e_7d, _ = resolve_duration_window("last_7d", now_utc=now)
    assert len(filter_logs_by_window(logs, s_7d, e_7d)) == 3

    s_30d, e_30d, _ = resolve_duration_window("last_30d", now_utc=now)
    assert len(filter_logs_by_window(logs, s_30d, e_30d)) == 4

    s_all, e_all, lbl_all = resolve_duration_window("all", now_utc=now)
    assert lbl_all == "All Recorded Activity"
    assert len(filter_logs_by_window(logs, s_all, e_all)) == 5

    s_cust, e_cust, lbl_cust = resolve_duration_window(
        "custom",
        start_date="2026-11-10",
        end_date="2026-11-24",
        tz_offset_minutes=0,
        now_utc=now,
    )
    assert "2026-11-10 to 2026-11-24" in lbl_cust
    assert [r.log_id for r in filter_logs_by_window(logs, s_cust, e_cust)] == ["log-3", "log-4"]


def test_generate_excel_and_pdf_reports_user_friendly() -> None:
    now = datetime(2026, 11, 26, 13, 0, 0, tzinfo=timezone.utc)
    logs, states = _sample_records(now)
    start_utc, end_utc, period_label = resolve_duration_window("last_7d", now_utc=now)
    filtered = filter_logs_by_window(logs, start_utc, end_utc)

    report = build_report_data(
        logs=filtered,
        states=states,
        period_label=period_label,
        tz_offset_minutes=330,
        now_utc=now,
    )
    assert report.total_events == 3
    assert report.total_returns == 1
    assert report.total_exits == 1
    assert report.total_internal_shifts == 1
    assert report.rows[0]["activity"] == "Returned to Campus (Room 1)"
    assert report.rows[0]["away_duration"] == "5m 25s"

    xlsx_bytes = generate_excel_report(report)
    assert xlsx_bytes[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes), "r") as zf:
        names = set(zf.namelist())
        assert "xl/workbook.xml" in names
        workbook_xml = zf.read("xl/workbook.xml").decode("utf-8")
        assert "Summary" in workbook_xml
        assert "Movement Details" in workbook_xml
        shared_xml = zf.read("xl/sharedStrings.xml").decode("utf-8")
        assert "Returned to Campus (Room 1)" in shared_xml
        assert "5m 25s" in shared_xml
        assert "CAMPUS_RETURN" not in shared_xml

    pdf_bytes = generate_pdf_report(report)
    assert pdf_bytes[:5] == b"%PDF-"
    assert b"%%EOF" in pdf_bytes[-64:]
    assert len(pdf_bytes) > 2000


def test_empty_reports_generate_cleanly() -> None:
    now = datetime(2026, 11, 26, 13, 0, 0, tzinfo=timezone.utc)
    report = build_report_data(
        logs=[],
        states=[],
        period_label="Last 24 Hours",
        tz_offset_minutes=0,
        now_utc=now,
    )
    assert report.total_events == 0
    xlsx_bytes = generate_excel_report(report)
    pdf_bytes = generate_pdf_report(report)
    assert xlsx_bytes[:2] == b"PK"
    assert pdf_bytes[:5] == b"%PDF-"
