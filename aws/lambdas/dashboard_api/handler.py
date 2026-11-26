"""AWS Lambda handler for Authority Verification Dashboard REST API."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Callable, Dict, List, Optional

from shared.authority_actions import (
    AuthorizationError,
    LogNotFoundError,
    adjust_log_timestamp,
    assert_authority_role,
    export_logs_to_csv,
    flag_movement_log,
    verify_movement_log,
)
from shared.models import format_iso_utc
from shared.repository import DynamoDBStateRepository, StateRepository


def _json_response(status_code: int, payload: Any) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Authorization,Content-Type",
            "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
        },
        "body": json.dumps(payload),
    }


def _extract_actor_and_groups(event: Dict[str, Any]) -> tuple:
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    actor = claims.get("email") or claims.get("cognito:username") or "authority-user"
    raw_groups = claims.get("cognito:groups", [])
    if isinstance(raw_groups, str):
        groups = [g.strip() for g in raw_groups.strip("[]").split(",") if g.strip()]
    else:
        groups = list(raw_groups)
    return actor, groups


def handle_dashboard_request(
    method: str,
    path: str,
    query_params: Dict[str, str],
    body: Dict[str, Any],
    repo: StateRepository,
    actor: str = "authority-user",
    groups: Optional[List[str]] = None,
    snapshot_url_resolver: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Pure router shared by AWS Lambda and local dev HTTP server."""
    groups = groups if groups is not None else ["Authorities"]

    try:
        if method == "GET" and path.endswith("/api/state"):
            states = [s.model_dump() for s in repo.list_person_states()]
            return _json_response(
                200,
                {
                    "server_time": format_iso_utc(datetime.now(timezone.utc)),
                    "away_alert_threshold_seconds": float(os.environ.get("AWAY_ALERT_THRESHOLD_SECONDS", "900")),
                    "persons": states,
                },
            )

        if method == "GET" and path.endswith("/api/logs/export"):
            logs = repo.list_movement_logs(
                person_id=query_params.get("person_id") or None,
                verification_status=query_params.get("verification_status") or None,
                event_type=query_params.get("event_type") or None,
                limit=int(query_params.get("limit", "1000")),
            )
            csv_text = export_logs_to_csv(logs)
            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "text/csv",
                    "Content-Disposition": 'attachment; filename="workzone_movement_logs.csv"',
                    "Access-Control-Allow-Origin": "*",
                },
                "body": csv_text,
            }

        if method == "GET" and path.endswith("/api/logs"):
            logs = repo.list_movement_logs(
                person_id=query_params.get("person_id") or None,
                verification_status=query_params.get("verification_status") or None,
                event_type=query_params.get("event_type") or None,
                limit=int(query_params.get("limit", "200")),
            )
            serialized = []
            for log in logs:
                item = log.model_dump()
                if log.snapshot_s3_key and snapshot_url_resolver:
                    item["snapshot_url"] = snapshot_url_resolver(log.snapshot_s3_key)
                else:
                    item["snapshot_url"] = log.snapshot_s3_key
                serialized.append(item)
            return _json_response(200, {"logs": serialized})

        # Mutations require Authorities group
        if method == "POST" and path.endswith("/verify"):
            assert_authority_role(groups)
            log_id = path.split("/")[-2]
            updated = verify_movement_log(
                repo=repo,
                log_id=log_id,
                actor=actor,
                remarks=body.get("remarks"),
                assign_person_id=body.get("assign_person_id"),
                assign_display_name=body.get("assign_display_name"),
            )
            return _json_response(200, {"log": updated.model_dump()})

        if method == "POST" and path.endswith("/flag"):
            assert_authority_role(groups)
            log_id = path.split("/")[-2]
            updated = flag_movement_log(
                repo=repo,
                log_id=log_id,
                actor=actor,
                remarks=str(body.get("remarks", "")),
            )
            return _json_response(200, {"log": updated.model_dump()})

        if method == "PATCH" and path.endswith("/adjust"):
            assert_authority_role(groups)
            log_id = path.split("/")[-2]
            updated = adjust_log_timestamp(
                repo=repo,
                log_id=log_id,
                new_timestamp=str(body.get("new_timestamp", "")),
                actor=actor,
                remarks=str(body.get("remarks", "")),
            )
            return _json_response(200, {"log": updated.model_dump()})

        return _json_response(404, {"error": f"Route not found: {method} {path}"})

    except AuthorizationError as exc:
        return _json_response(403, {"error": str(exc)})
    except LogNotFoundError as exc:
        return _json_response(404, {"error": str(exc)})
    except ValueError as exc:
        return _json_response(400, {"error": str(exc)})


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    repo = DynamoDBStateRepository(
        active_state_table_name=os.environ["ACTIVE_STATE_TABLE"],
        movement_logs_table_name=os.environ["MOVEMENT_LOGS_TABLE"],
        processed_events_table_name=os.environ["PROCESSED_EVENTS_TABLE"],
    )
    bucket = os.environ.get("SNAPSHOTS_BUCKET")
    s3_client = None
    if bucket:
        import boto3
        s3_client = boto3.client("s3")

    def resolve_snapshot(s3_key: str) -> str:
        if not s3_client or not bucket:
            return s3_key
        return s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=900,
        )

    method = event.get("requestContext", {}).get("http", {}).get("method", "GET").upper()
    raw_path = event.get("rawPath", "")
    query = event.get("queryStringParameters") or {}
    body = json.loads(event.get("body") or "{}") if event.get("body") else {}
    actor, groups = _extract_actor_and_groups(event)

    return handle_dashboard_request(
        method=method,
        path=raw_path,
        query_params=query,
        body=body,
        repo=repo,
        actor=actor,
        groups=groups,
        snapshot_url_resolver=resolve_snapshot,
    )
