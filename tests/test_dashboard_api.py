"""Unit tests for Dashboard API endpoints, Authority RBAC, timestamp adjustments, and CSV export."""

from __future__ import annotations

import json
from pathlib import Path

from dashboard_api.handler import handle_dashboard_request
from tests.mock_aws_store import MockLocalStateStore


def test_authority_verify_flag_adjust_and_rbac(tmp_path: Path) -> None:
    store = MockLocalStateStore(db_path=str(tmp_path / "state.db"), snapshots_dir=str(tmp_path / "s3"))
    try:
        store.ingest_edge_event({
            "event_id": "a1",
            "person_id": "001",
            "display_name": "Student 1",
            "camera_id": "cam_1",
            "direction": "CAM1_INWARD",
            "timestamp": "2026-11-26T09:00:00Z",
            "confidence": 0.92,
        })
        exit_res = store.ingest_edge_event({
            "event_id": "a2",
            "person_id": "001",
            "display_name": "Student 1",
            "camera_id": "cam_1",
            "direction": "CAM1_OUTWARD",
            "timestamp": "2026-11-26T10:00:00Z",
            "confidence": 0.92,
        })
        ret_res = store.ingest_edge_event({
            "event_id": "a3",
            "person_id": "001",
            "display_name": "Student 1",
            "camera_id": "cam_1",
            "direction": "CAM1_INWARD",
            "timestamp": "2026-11-26T10:10:00Z",
            "confidence": 0.92,
        })
        assert ret_res["away_duration_seconds"] == 600.0

        # Viewers role must be rejected on mutations
        forbidden = handle_dashboard_request(
            method="POST",
            path=f"/api/logs/{ret_res['log_id']}/verify",
            query_params={},
            body={"remarks": "Try verify"},
            repo=store,
            actor="viewer@workzone.local",
            groups=["Viewers"],
        )
        assert forbidden["statusCode"] == 403

        # Authority adjusts return timestamp to 10:15:00Z -> away_duration becomes 900.0s
        adj = handle_dashboard_request(
            method="PATCH",
            path=f"/api/logs/{ret_res['log_id']}/adjust",
            query_params={},
            body={"new_timestamp": "2026-11-26T10:15:00Z", "remarks": "NTP clock offset"},
            repo=store,
            actor="warden@workzone.local",
            groups=["Authorities"],
        )
        assert adj["statusCode"] == 200
        adj_log = json.loads(adj["body"])["log"]
        assert adj_log["away_duration_seconds"] == 900.0
        assert adj_log["verification_status"] == "VERIFIED"
        assert len(adj_log["audit_history"]) == 1

        # Flag exit log
        flg = handle_dashboard_request(
            method="POST",
            path=f"/api/logs/{exit_res['log_id']}/flag",
            query_params={},
            body={"remarks": "Camera occlusion review"},
            repo=store,
            actor="warden@workzone.local",
            groups=["Authorities"],
        )
        assert flg["statusCode"] == 200
        assert json.loads(flg["body"])["log"]["verification_status"] == "FLAGGED"

        # CSV export includes updated values
        csv_resp = handle_dashboard_request(
            method="GET",
            path="/api/logs/export",
            query_params={},
            body={},
            repo=store,
            groups=["Viewers"],
        )
        assert csv_resp["statusCode"] == 200
        assert "900.00" in csv_resp["body"]
        assert "NTP clock offset" in csv_resp["body"]
    finally:
        store.close()
