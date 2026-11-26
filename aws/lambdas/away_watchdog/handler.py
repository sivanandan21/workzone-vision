"""Scheduled Lambda watchdog alerting via SNS while a person stays OUTSIDE past threshold."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional

from shared.models import ZoneState, parse_iso_utc
from shared.repository import DynamoDBStateRepository, StateRepository


def check_overdue_outside_persons(
    repo: StateRepository,
    away_alert_threshold_seconds: float = 900.0,
    now_utc: Optional[datetime] = None,
    sns_client: Any = None,
    sns_topic_arn: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find persons currently OUTSIDE longer than threshold and emit one-shot SNS alert per exit."""
    now = now_utc or datetime.now(timezone.utc)
    alerts: List[Dict[str, Any]] = []

    for state in repo.list_person_states():
        if state.current_zone != ZoneState.OUTSIDE or not state.last_exit_time or state.away_alert_sent:
            continue

        elapsed = (now - parse_iso_utc(state.last_exit_time)).total_seconds()
        if elapsed > away_alert_threshold_seconds:
            alert_payload = {
                "alert_type": "AWAY_DURATION_OVERDUE_LIVE",
                "person_id": state.person_id,
                "display_name": state.display_name,
                "elapsed_away_seconds": round(elapsed, 1),
                "threshold_seconds": away_alert_threshold_seconds,
                "last_exit_time": state.last_exit_time,
            }
            if sns_topic_arn:
                if sns_client is None:
                    import boto3
                    sns_client = boto3.client("sns")
                sns_client.publish(
                    TopicArn=sns_topic_arn,
                    Subject=f"[WorkZone Vision] Overdue Outside Alert: {state.display_name}",
                    Message=json.dumps(alert_payload),
                )
            state.away_alert_sent = True
            repo.save_person_state(state)
            alerts.append(alert_payload)

    return alerts


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    repo = DynamoDBStateRepository(
        active_state_table_name=os.environ["ACTIVE_STATE_TABLE"],
        movement_logs_table_name=os.environ["MOVEMENT_LOGS_TABLE"],
        processed_events_table_name=os.environ["PROCESSED_EVENTS_TABLE"],
    )
    threshold = float(os.environ.get("AWAY_ALERT_THRESHOLD_SECONDS", "900"))
    topic_arn = os.environ.get("AWAY_ALERTS_TOPIC_ARN")
    alerts = check_overdue_outside_persons(
        repo=repo,
        away_alert_threshold_seconds=threshold,
        sns_topic_arn=topic_arn,
    )
    return {"alerts_sent": len(alerts), "alerts": alerts}
