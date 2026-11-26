"""User-friendly PDF and Excel (.xlsx) report generator with duration filtering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
import io
import sys
from typing import Any, Dict, List, Optional, Tuple

from shared.models import (
    ActivePersonStateRecord,
    MovementEventType,
    MovementLogRecord,
    VerificationStatus,
    ZoneState,
    parse_iso_utc,
)

_EVENT_LABELS: Dict[str, str] = {
    MovementEventType.CAMPUS_ENTRY.value: "Entered Campus (Room 1)",
    MovementEventType.CAMPUS_RETURN.value: "Returned to Campus (Room 1)",
    MovementEventType.INTERNAL_SHIFT_R1_TO_R2.value: "Moved Room 1 to Room 2",
    MovementEventType.INTERNAL_SHIFT_R2_TO_R1.value: "Moved Room 2 to Room 1",
    MovementEventType.CAMPUS_EXIT.value: "Left Campus (Outside)",
    MovementEventType.CAMPUS_EXIT_RECONCILED.value: "Left Campus (Auto-Reconciled)",
    MovementEventType.CAMPUS_ENTRY_RECONCILED.value: "Entered Campus (Auto-Reconciled)",
    MovementEventType.ANOMALY_REVIEW.value: "Needs Attention",
}

_ZONE_LABELS: Dict[str, str] = {
    ZoneState.OUTSIDE.value: "Outside Campus",
    ZoneState.IN_ROOM_1.value: "Room 1 (Outer)",
    ZoneState.IN_ROOM_2.value: "Room 2 (Inner)",
}

_CAMERA_LABELS: Dict[str, str] = {
    "cam_1": "Camera 1 (Main Door)",
    "cam_2": "Camera 2 (Connecting Door)",
}

_STATUS_LABELS: Dict[str, str] = {
    VerificationStatus.AUTO_MATCHED.value: "Auto Matched",
    VerificationStatus.VERIFIED.value: "Verified",
    VerificationStatus.PENDING_REVIEW.value: "Pending Review",
    VerificationStatus.FLAGGED.value: "Flagged",
}

_DURATION_TITLES: Dict[str, str] = {
    "today": "Today",
    "last_24h": "Last 24 Hours",
    "last_7d": "Last 7 Days",
    "last_30d": "Last 30 Days",
    "all": "All Recorded Activity",
    "custom": "Custom Date Range",
}


def format_human_duration(seconds: Optional[float]) -> str:
    """Format seconds into a readable string like '12m 30s' or '1h 05m'."""
    if seconds is None:
        return "—"
    total = max(0, int(round(seconds)))
    hrs, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}h {mins:02d}m {secs:02d}s"
    if mins > 0:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


def resolve_duration_window(
    duration: str = "last_7d",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    tz_offset_minutes: int = 0,
    now_utc: Optional[datetime] = None,
) -> Tuple[Optional[datetime], Optional[datetime], str]:
    """Compute UTC [start, end] window and human-friendly period description."""
    now = now_utc or datetime.now(timezone.utc)
    offset = timedelta(minutes=int(tz_offset_minutes))
    local_tz = timezone(offset)
    now_local = now.astimezone(local_tz)
    key = (duration or "last_7d").strip().lower()

    if key == "today":
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_local.astimezone(timezone.utc), now, f"Today ({start_local.strftime('%d %b %Y')})"

    if key == "last_24h":
        start_utc = now - timedelta(hours=24)
        return start_utc, now, "Last 24 Hours"

    if key == "last_7d":
        start_utc = now - timedelta(days=7)
        return start_utc, now, "Last 7 Days"

    if key == "last_30d":
        start_utc = now - timedelta(days=30)
        return start_utc, now, "Last 30 Days"

    if key == "custom" and (start_date or end_date):
        start_utc: Optional[datetime] = None
        end_utc: Optional[datetime] = None
        if start_date:
            sd = datetime.strptime(start_date.strip()[:10], "%Y-%m-%d").replace(
                hour=0, minute=0, second=0, tzinfo=local_tz
            )
            start_utc = sd.astimezone(timezone.utc)
        if end_date:
            ed = datetime.strptime(end_date.strip()[:10], "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=local_tz
            )
            end_utc = ed.astimezone(timezone.utc)
        label = f"Custom Range ({start_date or '...'} to {end_date or '...'})"
        return start_utc, end_utc, label

    return None, None, _DURATION_TITLES.get(key, "All Recorded Activity")


def filter_logs_by_window(
    logs: List[MovementLogRecord],
    start_utc: Optional[datetime],
    end_utc: Optional[datetime],
) -> List[MovementLogRecord]:
    """Filter MovementLogRecord list inclusively by [start_utc, end_utc]."""
    filtered: List[MovementLogRecord] = []
    for log in logs:
        dt = parse_iso_utc(log.timestamp)
        if start_utc is not None and dt < start_utc:
            continue
        if end_utc is not None and dt > end_utc:
            continue
        filtered.append(log)
    return filtered


@dataclass
class ReportData:
    period_label: str
    generated_at_label: str
    tz_offset_minutes: int
    total_events: int
    total_entries: int
    total_exits: int
    total_internal_shifts: int
    total_returns: int
    avg_away_seconds: Optional[float]
    max_away_seconds: Optional[float]
    student_summaries: List[Dict[str, Any]]
    rows: List[Dict[str, Any]]


def build_report_data(
    logs: List[MovementLogRecord],
    states: List[ActivePersonStateRecord],
    period_label: str,
    tz_offset_minutes: int = 0,
    now_utc: Optional[datetime] = None,
) -> ReportData:
    """Transform raw logs & active states into clean, human-readable report structures."""
    now = now_utc or datetime.now(timezone.utc)
    local_tz = timezone(timedelta(minutes=int(tz_offset_minutes)))
    generated_label = now.astimezone(local_tz).strftime("%d %b %Y, %I:%M %p")

    state_map = {s.person_id: s for s in states}
    per_student: Dict[str, Dict[str, Any]] = {}

    def _ensure_student(pid: str, name: str) -> Dict[str, Any]:
        if pid not in per_student:
            st = state_map.get(pid)
            zone_label = _ZONE_LABELS.get(st.current_zone.value, st.current_zone.value) if st else "Unknown"
            per_student[pid] = {
                "student": f"{name} ({pid})",
                "person_id": pid,
                "current_location": zone_label,
                "entries": 0,
                "exits": 0,
                "internal_shifts": 0,
                "returns": 0,
                "away_durations": [],
            }
        return per_student[pid]

    for s in states:
        _ensure_student(s.person_id, s.display_name)

    rows: List[Dict[str, Any]] = []
    all_away: List[float] = []
    total_entries = 0
    total_exits = 0
    total_shifts = 0
    total_returns = 0

    for log in logs:
        st_rec = _ensure_student(log.person_id, log.display_name)
        ev = log.event_type.value
        if ev in (MovementEventType.CAMPUS_ENTRY.value, MovementEventType.CAMPUS_ENTRY_RECONCILED.value):
            st_rec["entries"] += 1
            total_entries += 1
        elif ev == MovementEventType.CAMPUS_RETURN.value:
            st_rec["returns"] += 1
            total_returns += 1
        elif ev in (MovementEventType.INTERNAL_SHIFT_R1_TO_R2.value, MovementEventType.INTERNAL_SHIFT_R2_TO_R1.value):
            st_rec["internal_shifts"] += 1
            total_shifts += 1
        elif ev in (MovementEventType.CAMPUS_EXIT.value, MovementEventType.CAMPUS_EXIT_RECONCILED.value):
            st_rec["exits"] += 1
            total_exits += 1

        if log.away_duration_seconds is not None:
            st_rec["away_durations"].append(log.away_duration_seconds)
            all_away.append(log.away_duration_seconds)

        local_dt = parse_iso_utc(log.timestamp).astimezone(local_tz)
        rows.append({
            "datetime": local_dt.strftime("%d %b %Y, %I:%M:%S %p"),
            "student": f"{log.display_name} ({log.person_id})",
            "activity": _EVENT_LABELS.get(ev, ev),
            "path": f"{_ZONE_LABELS.get(log.from_zone.value, log.from_zone.value)} -> {_ZONE_LABELS.get(log.to_zone.value, log.to_zone.value)}",
            "camera": _CAMERA_LABELS.get(log.camera_id, log.camera_id),
            "away_duration": format_human_duration(log.away_duration_seconds),
            "status": _STATUS_LABELS.get(log.verification_status.value, log.verification_status.value),
            "notes": log.remarks or "—",
        })

    student_summaries: List[Dict[str, Any]] = []
    for pid in sorted(per_student.keys()):
        item = per_student[pid]
        durs = item["away_durations"]
        total_away = sum(durs) if durs else None
        avg_away = (sum(durs) / len(durs)) if durs else None
        max_away = max(durs) if durs else None
        student_summaries.append({
            "student": item["student"],
            "current_location": item["current_location"],
            "entries": item["entries"],
            "exits": item["exits"],
            "internal_shifts": item["internal_shifts"],
            "returns": item["returns"],
            "total_away": format_human_duration(total_away),
            "avg_away": format_human_duration(avg_away),
            "longest_away": format_human_duration(max_away),
        })

    return ReportData(
        period_label=period_label,
        generated_at_label=generated_label,
        tz_offset_minutes=tz_offset_minutes,
        total_events=len(logs),
        total_entries=total_entries,
        total_exits=total_exits,
        total_internal_shifts=total_shifts,
        total_returns=total_returns,
        avg_away_seconds=(sum(all_away) / len(all_away)) if all_away else None,
        max_away_seconds=max(all_away) if all_away else None,
        student_summaries=student_summaries,
        rows=rows,
    )


def generate_excel_report(report: ReportData) -> bytes:
    """Create a styled multi-sheet Excel (.xlsx) workbook."""
    import xlsxwriter

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})

    title_fmt = wb.add_format({"bold": True, "font_size": 15, "font_color": "#FFFFFF", "bg_color": "#1E293B", "valign": "vcenter"})
    subtitle_fmt = wb.add_format({"font_size": 10, "font_color": "#CBD5E1", "bg_color": "#1E293B", "valign": "vcenter"})
    section_fmt = wb.add_format({"bold": True, "font_size": 11, "font_color": "#0F172A", "bottom": 2, "bottom_color": "#2563EB"})
    th_fmt = wb.add_format({"bold": True, "font_size": 10, "font_color": "#FFFFFF", "bg_color": "#2563EB", "border": 1, "border_color": "#1D4ED8", "align": "left", "valign": "vcenter"})
    kpi_label_fmt = wb.add_format({"bold": True, "font_size": 9, "font_color": "#475569", "bg_color": "#F1F5F9", "border": 1, "border_color": "#CBD5E1", "align": "center"})
    kpi_val_fmt = wb.add_format({"bold": True, "font_size": 13, "font_color": "#0F172A", "bg_color": "#FFFFFF", "border": 1, "border_color": "#CBD5E1", "align": "center", "valign": "vcenter"})
    cell_even = wb.add_format({"font_size": 10, "bg_color": "#FFFFFF", "border": 1, "border_color": "#E2E8F0", "valign": "vcenter", "text_wrap": True})
    cell_odd = wb.add_format({"font_size": 10, "bg_color": "#F8FAFC", "border": 1, "border_color": "#E2E8F0", "valign": "vcenter", "text_wrap": True})
    num_even = wb.add_format({"font_size": 10, "bg_color": "#FFFFFF", "border": 1, "border_color": "#E2E8F0", "align": "center", "valign": "vcenter"})
    num_odd = wb.add_format({"font_size": 10, "bg_color": "#F8FAFC", "border": 1, "border_color": "#E2E8F0", "align": "center", "valign": "vcenter"})
    status_formats = {
        "Verified": wb.add_format({"bold": True, "font_size": 10, "font_color": "#065F46", "bg_color": "#D1FAE5", "border": 1, "border_color": "#E2E8F0", "align": "center", "valign": "vcenter"}),
        "Auto Matched": wb.add_format({"bold": True, "font_size": 10, "font_color": "#1E40AF", "bg_color": "#DBEAFE", "border": 1, "border_color": "#E2E8F0", "align": "center", "valign": "vcenter"}),
        "Pending Review": wb.add_format({"bold": True, "font_size": 10, "font_color": "#92400E", "bg_color": "#FEF3C7", "border": 1, "border_color": "#E2E8F0", "align": "center", "valign": "vcenter"}),
        "Flagged": wb.add_format({"bold": True, "font_size": 10, "font_color": "#991B1B", "bg_color": "#FEE2E2", "border": 1, "border_color": "#E2E8F0", "align": "center", "valign": "vcenter"}),
    }

    # --- Sheet 1: Summary ---
    ws_sum = wb.add_worksheet("Summary")
    ws_sum.hide_gridlines(2)
    ws_sum.set_row(0, 28)
    ws_sum.set_row(1, 20)
    ws_sum.merge_range("A1:I1", "WorkZone Vision — Student Movement & Away-Duration Report", title_fmt)
    ws_sum.merge_range("A2:I2", f"Report Period: {report.period_label}   |   Generated: {report.generated_at_label}", subtitle_fmt)

    kpis = [
        ("Total Events", report.total_events),
        ("Campus Exits", report.total_exits),
        ("Completed Returns", report.total_returns),
        ("Room 1 <-> 2 Shifts", report.total_internal_shifts),
        ("Avg Away Time", format_human_duration(report.avg_away_seconds)),
        ("Longest Away Time", format_human_duration(report.max_away_seconds)),
    ]
    ws_sum.set_row(3, 18)
    ws_sum.set_row(4, 26)
    for idx, (lbl, val) in enumerate(kpis):
        ws_sum.write(3, idx, lbl, kpi_label_fmt)
        ws_sum.write(4, idx, val, kpi_val_fmt)

    ws_sum.write(6, 0, "Per-Student Activity Summary", section_fmt)
    sum_headers = [
        "Student",
        "Current Location",
        "Campus Entries",
        "Room Shifts",
        "Campus Exits",
        "Completed Returns",
        "Total Time Away",
        "Avg Away Time",
        "Longest Away Time",
    ]
    ws_sum.set_row(7, 24)
    for col, h in enumerate(sum_headers):
        ws_sum.write(7, col, h, th_fmt)

    if report.student_summaries:
        for r_idx, st in enumerate(report.student_summaries, start=8):
            ws_sum.set_row(r_idx, 20)
            cf = cell_even if r_idx % 2 == 0 else cell_odd
            nf = num_even if r_idx % 2 == 0 else num_odd
            ws_sum.write(r_idx, 0, st["student"], cf)
            ws_sum.write(r_idx, 1, st["current_location"], cf)
            ws_sum.write(r_idx, 2, st["entries"], nf)
            ws_sum.write(r_idx, 3, st["internal_shifts"], nf)
            ws_sum.write(r_idx, 4, st["exits"], nf)
            ws_sum.write(r_idx, 5, st["returns"], nf)
            ws_sum.write(r_idx, 6, st["total_away"], nf)
            ws_sum.write(r_idx, 7, st["avg_away"], nf)
            ws_sum.write(r_idx, 8, st["longest_away"], nf)
    else:
        ws_sum.merge_range(8, 0, 8, 8, "No student activity recorded during the selected duration.", cell_even)

    for col, width in enumerate([22, 18, 15, 13, 14, 18, 16, 15, 18]):
        ws_sum.set_column(col, col, width)

    # --- Sheet 2: Movement Details ---
    ws_log = wb.add_worksheet("Movement Details")
    ws_log.hide_gridlines(2)
    ws_log.set_row(0, 28)
    ws_log.set_row(1, 20)
    ws_log.merge_range("A1:H1", "Detailed Movement & Away-Duration History", title_fmt)
    ws_log.merge_range("A2:H2", f"Report Period: {report.period_label}   |   Total Records: {report.total_events}", subtitle_fmt)

    log_headers = [
        "Date & Time",
        "Student",
        "Activity",
        "Movement Path",
        "Camera",
        "Away Duration",
        "Verification",
        "Notes",
    ]
    ws_log.set_row(3, 24)
    for col, h in enumerate(log_headers):
        ws_log.write(3, col, h, th_fmt)

    if report.rows:
        for r_idx, row in enumerate(report.rows, start=4):
            ws_log.set_row(r_idx, 22)
            cf = cell_even if r_idx % 2 == 0 else cell_odd
            nf = num_even if r_idx % 2 == 0 else num_odd
            sf = status_formats.get(row["status"], nf)
            ws_log.write(r_idx, 0, row["datetime"], cf)
            ws_log.write(r_idx, 1, row["student"], cf)
            ws_log.write(r_idx, 2, row["activity"], cf)
            ws_log.write(r_idx, 3, row["path"], cf)
            ws_log.write(r_idx, 4, row["camera"], cf)
            ws_log.write(r_idx, 5, row["away_duration"], nf)
            ws_log.write(r_idx, 6, row["status"], sf)
            ws_log.write(r_idx, 7, row["notes"], cf)
        ws_log.autofilter(3, 0, 3 + len(report.rows), 7)
    else:
        ws_log.merge_range(4, 0, 4, 7, "No movement events recorded during the selected duration.", cell_even)

    ws_log.freeze_panes(4, 0)
    for col, width in enumerate([24, 20, 28, 30, 26, 15, 16, 34]):
        ws_log.set_column(col, col, width)

    wb.close()
    return buf.getvalue()


def generate_pdf_report(report: ReportData) -> bytes:
    """Create a clean, user-friendly A4 Landscape PDF report using ReportLab."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    if sys.version_info < (3, 9):
        import hashlib
        from reportlab.pdfbase import pdfdoc
        if getattr(pdfdoc.md5, "__name__", "") != "<lambda>":
            pdfdoc.md5 = lambda data=b"", **_kw: hashlib.md5(data)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="WorkZone Vision Movement Report",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "WZTitle",
        parent=styles["Heading1"],
        fontSize=16,
        textColor=colors.white,
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        "WZSub",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#CBD5E1"),
    )
    section_style = ParagraphStyle(
        "WZSection",
        parent=styles["Heading2"],
        fontSize=11,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=8,
        spaceAfter=4,
    )
    cell_style = ParagraphStyle(
        "WZCell",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#1E293B"),
    )
    header_cell_style = ParagraphStyle(
        "WZHead",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=colors.white,
        fontName="Helvetica-Bold",
    )

    elements: List[Any] = []
    esc = html.escape

    # Header Banner
    banner = Table(
        [
            [Paragraph("WorkZone Vision — Movement &amp; Away-Duration Report", title_style)],
            [Paragraph(f"Period: <b>{esc(report.period_label)}</b> &nbsp;&nbsp;|&nbsp;&nbsp; Generated: {esc(report.generated_at_label)}", subtitle_style)],
        ],
        colWidths=[268 * mm],
    )
    banner.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (0, 0), 8),
            ("BOTTOMPADDING", (0, 1), (0, 1), 8),
        ])
    )
    elements.append(banner)
    elements.append(Spacer(1, 6 * mm))

    # KPI Strip
    kpi_table = Table(
        [
            ["Total Events", "Campus Exits", "Completed Returns", "Room 1 <-> 2 Shifts", "Average Away Time", "Longest Away Time"],
            [
                str(report.total_events),
                str(report.total_exits),
                str(report.total_returns),
                str(report.total_internal_shifts),
                format_human_duration(report.avg_away_seconds),
                format_human_duration(report.max_away_seconds),
            ],
        ],
        colWidths=[44.6 * mm] * 6,
    )
    kpi_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#475569")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 1), (-1, 1), 12),
            ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#0F172A")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])
    )
    elements.append(kpi_table)
    elements.append(Spacer(1, 5 * mm))

    # Per-Student Summary Table
    elements.append(Paragraph("Student Activity Summary", section_style))
    sum_rows = [
        [
            Paragraph("Student", header_cell_style),
            Paragraph("Current Location", header_cell_style),
            Paragraph("Entries", header_cell_style),
            Paragraph("Room Shifts", header_cell_style),
            Paragraph("Exits", header_cell_style),
            Paragraph("Returns", header_cell_style),
            Paragraph("Total Away", header_cell_style),
            Paragraph("Avg Away", header_cell_style),
            Paragraph("Longest Away", header_cell_style),
        ]
    ]
    if report.student_summaries:
        for st in report.student_summaries:
            sum_rows.append([
                Paragraph(esc(str(st["student"])), cell_style),
                Paragraph(esc(str(st["current_location"])), cell_style),
                Paragraph(str(st["entries"]), cell_style),
                Paragraph(str(st["internal_shifts"]), cell_style),
                Paragraph(str(st["exits"]), cell_style),
                Paragraph(str(st["returns"]), cell_style),
                Paragraph(esc(str(st["total_away"])), cell_style),
                Paragraph(esc(str(st["avg_away"])), cell_style),
                Paragraph(esc(str(st["longest_away"])), cell_style),
            ])
    else:
        sum_rows.append([Paragraph("No student activity recorded during this period.", cell_style)] + [""] * 8)

    sum_table = Table(
        sum_rows,
        colWidths=[42 * mm, 38 * mm, 20 * mm, 24 * mm, 20 * mm, 22 * mm, 34 * mm, 34 * mm, 34 * mm],
        repeatRows=1,
    )
    sum_table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563EB")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]
    if not report.student_summaries:
        sum_table_style.append(("SPAN", (0, 1), (-1, 1)))
    sum_table.setStyle(TableStyle(sum_table_style))
    elements.append(sum_table)
    elements.append(Spacer(1, 5 * mm))

    # Detailed Movement Log Table
    elements.append(Paragraph("Detailed Movement History", section_style))
    log_rows = [
        [
            Paragraph("Date &amp; Time", header_cell_style),
            Paragraph("Student", header_cell_style),
            Paragraph("Activity", header_cell_style),
            Paragraph("Movement Path", header_cell_style),
            Paragraph("Camera", header_cell_style),
            Paragraph("Away Time", header_cell_style),
            Paragraph("Status", header_cell_style),
            Paragraph("Notes", header_cell_style),
        ]
    ]
    if report.rows:
        for row in report.rows:
            log_rows.append([
                Paragraph(esc(str(row["datetime"])), cell_style),
                Paragraph(esc(str(row["student"])), cell_style),
                Paragraph(esc(str(row["activity"])), cell_style),
                Paragraph(esc(str(row["path"])), cell_style),
                Paragraph(esc(str(row["camera"])), cell_style),
                Paragraph(esc(str(row["away_duration"])), cell_style),
                Paragraph(esc(str(row["status"])), cell_style),
                Paragraph(esc(str(row["notes"])), cell_style),
            ])
    else:
        log_rows.append([Paragraph("No movement events recorded during the selected duration.", cell_style)] + [""] * 7)

    log_table = Table(
        log_rows,
        colWidths=[40 * mm, 30 * mm, 42 * mm, 44 * mm, 34 * mm, 22 * mm, 24 * mm, 32 * mm],
        repeatRows=1,
    )
    log_table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]
    if not report.rows:
        log_table_style.append(("SPAN", (0, 1), (-1, 1)))
    log_table.setStyle(TableStyle(log_table_style))
    elements.append(log_table)

    def _draw_footer(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(14 * mm, 8 * mm, f"WorkZone Vision Report · {report.period_label}")
        canvas.drawRightString(283 * mm, 8 * mm, f"Page {doc_obj.page}")
        canvas.restoreState()

    doc.build(elements, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buf.getvalue()
