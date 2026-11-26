"""AWS Lambda handler for edge crossing ingestion (HTTP API Gateway & IoT Rule)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from shared.models import CrossingEventPayload
from shared.repository import DynamoDBStateRepository, StateRepository
from shared.state_machine import apply_crossing_event


def _build_default_repo() -> StateRepository:
    return DynamoDBStateRepository(
        active_state_table_name=os.environ["ACTIVE_STATE_TABLE"],
        movement_logs_table_name=os.environ["MOVEMENT_LOGS_TABLE"],
        processed_events_table_name=os.environ["PROCESSED_EVENTS_TABLE"],
    )


def process_crossing_payload(
    payload_dict: Dict[str, Any],
    repo: StateRepository,
    away_alert_threshold_seconds: float = 900.0,
    sns_client: Any = None,
    sns_topic_arn: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate payload, ensure idempotency, run nested-zone state machine, and emit SNS alert if needed."""
    event = CrossingEventPayload(**payload_dict)

    if not repo.claim_event_id(event.event_id):
        return {
            "status": "duplicate_ignored",
            "event_id": event.event_id,
        }

    current_state = repo.get_person_state(event.person_id)
    transition = apply_crossing_event(
        current_state=current_state,
        event=event,
        away_alert_threshold_seconds=away_alert_threshold_seconds,
    )

    repo.save_person_state(transition.new_state)
    repo.save_movement_log(transition.log_record)

    if transition.should_send_away_alert and sns_topic_arn:
        if sns_client is None:
            import boto3
            sns_client = boto3.client("sns")
        sns_client.publish(
            TopicArn=sns_topic_arn,
            Subject=f"[WorkZone Vision] Away Duration Exceeded: {transition.new_state.display_name}",
            Message=json.dumps({
                "alert_type": "AWAY_DURATION_EXCEEDED_ON_RETURN",
                "person_id": event.person_id,
                "display_name": transition.new_state.display_name,
                "away_duration_seconds": transition.log_record.away_duration_seconds,
                "threshold_seconds": away_alert_threshold_seconds,
                "timestamp": transition.log_record.timestamp,
                "reason": transition.alert_reason,
            }),
        )

    return {
        "status": "processed",
        "event_id": event.event_id,
        "log_id": transition.log_record.log_id,
        "person_id": event.person_id,
        "from_zone": transition.log_record.from_zone.value,
        "to_zone": transition.log_record.to_zone.value,
        "event_type": transition.log_record.event_type.value,
        "away_duration_seconds": transition.log_record.away_duration_seconds,
        "verification_status": transition.log_record.verification_status.value,
        "alert_triggered": transition.should_send_away_alert,
    }


def _generate_presigned_upload(body: Dict[str, Any]) -> Dict[str, Any]:
    import boto3
    bucket = os.environ["SNAPSHOTS_BUCKET"]
    s3_key = str(body.get("s3_key", "")).lstrip("/")
    if not s3_key.startswith("snapshots/"):
        s3_key = f"snapshots/{s3_key}"
    s3 = boto3.client("s3")
    upload_url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": s3_key, "ContentType": "image/jpeg"},
        ExpiresIn=300,
    )
    return {"s3_key": s3_key, "upload_url": upload_url}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda entry point handling HTTP API Gateway v2 and IoT Core triggers."""
    threshold = float(os.environ.get("AWAY_ALERT_THRESHOLD_SECONDS", "900"))
    topic_arn = os.environ.get("AWAY_ALERTS_TOPIC_ARN")

    # HTTP API Gateway trigger
    if "requestContext" in event:
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        expected_key = os.environ.get("EDGE_INGEST_API_KEY", "")
        if expected_key and headers.get("x-edge-api-key") != expected_key:
            return {"statusCode": 401, "body": json.dumps({"error": "Invalid X-Edge-Api-Key"})}

        raw_path = event.get("rawPath", "")
        body = json.loads(event.get("body") or "{}")
        if raw_path.endswith("/ingest/presign"):
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(_generate_presigned_upload(body)),
            }

        repo = _build_default_repo()
        result = process_crossing_payload(
            payload_dict=body,
            repo=repo,
            away_alert_threshold_seconds=threshold,
            sns_topic_arn=topic_arn,
        )
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result),
        }

    # Direct AWS IoT Rule invocation (event IS the JSON payload)
    repo = _build_default_repo()
    return process_crossing_payload(
        payload_dict=event,
        repo=repo,
        away_alert_threshold_seconds=threshold,
        sns_topic_arn=topic_arn,
    )
