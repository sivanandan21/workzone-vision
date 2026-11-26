"""State repository abstraction and Amazon DynamoDB implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
import time
from typing import Any, Dict, List, Optional

from shared.models import (
    ActivePersonStateRecord,
    MovementLogRecord,
)


def _to_dynamodb(obj: Any) -> Any:
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamodb(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_to_dynamodb(v) for v in obj]
    return obj


def _from_dynamodb(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    if isinstance(obj, dict):
        return {k: _from_dynamodb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_dynamodb(v) for v in obj]
    return obj


class StateRepository(ABC):
    """Persistence contract implemented by DynamoDB and MockLocalStateStore."""

    @abstractmethod
    def claim_event_id(self, event_id: str, ttl_seconds: int = 86400 * 7) -> bool:
        """Atomically mark event_id as processed. Returns False if already processed."""

    @abstractmethod
    def get_person_state(self, person_id: str) -> Optional[ActivePersonStateRecord]:
        """Fetch current ActivePersonState for a person."""

    @abstractmethod
    def save_person_state(self, state: ActivePersonStateRecord) -> None:
        """Upsert ActivePersonState."""

    @abstractmethod
    def list_person_states(self) -> List[ActivePersonStateRecord]:
        """List all ActivePersonState records."""

    @abstractmethod
    def save_movement_log(self, log: MovementLogRecord) -> None:
        """Insert or update a MovementLogs record."""

    @abstractmethod
    def get_movement_log(self, log_id: str) -> Optional[MovementLogRecord]:
        """Fetch a single MovementLogs entry by log_id."""

    @abstractmethod
    def list_movement_logs(
        self,
        person_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[MovementLogRecord]:
        """Query MovementLogs sorted descending by timestamp."""

    @abstractmethod
    def find_paired_return_log(self, exit_log_id: str) -> Optional[MovementLogRecord]:
        """Find the CAMPUS_RETURN log referencing a given CAMPUS_EXIT log_id."""


class DynamoDBStateRepository(StateRepository):
    """Amazon DynamoDB implementation of StateRepository."""

    def __init__(
        self,
        active_state_table_name: str,
        movement_logs_table_name: str,
        processed_events_table_name: str,
        dynamodb_resource: Any = None,
    ) -> None:
        if dynamodb_resource is None:
            import boto3
            dynamodb_resource = boto3.resource("dynamodb")
        self._state_table = dynamodb_resource.Table(active_state_table_name)
        self._logs_table = dynamodb_resource.Table(movement_logs_table_name)
        self._events_table = dynamodb_resource.Table(processed_events_table_name)

    def claim_event_id(self, event_id: str, ttl_seconds: int = 86400 * 7) -> bool:
        try:
            self._events_table.put_item(
                Item={
                    "event_id": event_id,
                    "ttl_epoch": int(time.time()) + int(ttl_seconds),
                },
                ConditionExpression="attribute_not_exists(event_id)",
            )
            return True
        except Exception as exc:
            # ConditionalCheckFailedException indicates duplicate delivery
            if exc.__class__.__name__ == "ConditionalCheckFailedException":
                return False
            raise

    def get_person_state(self, person_id: str) -> Optional[ActivePersonStateRecord]:
        resp = self._state_table.get_item(Key={"person_id": person_id})
        item = resp.get("Item")
        if not item:
            return None
        return ActivePersonStateRecord(**_from_dynamodb(item))

    def save_person_state(self, state: ActivePersonStateRecord) -> None:
        self._state_table.put_item(Item=_to_dynamodb(state.model_dump()))

    def list_person_states(self) -> List[ActivePersonStateRecord]:
        items: List[Dict[str, Any]] = []
        resp = self._state_table.scan()
        items.extend(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = self._state_table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
            items.extend(resp.get("Items", []))
        return [ActivePersonStateRecord(**_from_dynamodb(i)) for i in items]

    def save_movement_log(self, log: MovementLogRecord) -> None:
        self._logs_table.put_item(Item=_to_dynamodb(log.model_dump()))

    def get_movement_log(self, log_id: str) -> Optional[MovementLogRecord]:
        resp = self._logs_table.get_item(Key={"log_id": log_id})
        item = resp.get("Item")
        if not item:
            return None
        return MovementLogRecord(**_from_dynamodb(item))

    def list_movement_logs(
        self,
        person_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 200,
    ) -> List[MovementLogRecord]:
        from boto3.dynamodb.conditions import Key

        if person_id:
            resp = self._logs_table.query(
                IndexName="PersonTimeIndex",
                KeyConditionExpression=Key("person_id").eq(person_id),
                ScanIndexForward=False,
                Limit=max(limit, 100),
            )
        else:
            resp = self._logs_table.query(
                IndexName="TimestampIndex",
                KeyConditionExpression=Key("gsi_pk").eq("LOG"),
                ScanIndexForward=False,
                Limit=max(limit, 100),
            )

        records = [MovementLogRecord(**_from_dynamodb(i)) for i in resp.get("Items", [])]
        if verification_status:
            records = [r for r in records if r.verification_status.value == verification_status]
        if event_type:
            records = [r for r in records if r.event_type.value == event_type]
        return records[:limit]

    def find_paired_return_log(self, exit_log_id: str) -> Optional[MovementLogRecord]:
        # Recent logs scan/query to locate return referencing paired_exit_log_id
        for log in self.list_movement_logs(limit=500):
            if log.paired_exit_log_id == exit_log_id:
                return log
        return None
