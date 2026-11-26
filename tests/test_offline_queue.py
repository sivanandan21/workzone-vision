"""Unit tests for SQLite WAL offline queue and automatic cloud recovery flush."""

from __future__ import annotations

from pathlib import Path

from edge.src.config import CloudConfig
from edge.src.offline_queue import OfflineEventQueue
from edge.src.publisher import CloudEventPublisher
from tests.mock_aws_store import MockLocalStateStore


def test_offline_queue_buffers_on_outage_and_flushes_on_recovery(tmp_path: Path) -> None:
    queue = OfflineEventQueue(str(tmp_path / "outbox.db"))
    store = MockLocalStateStore(db_path=str(tmp_path / "state.db"), snapshots_dir=str(tmp_path / "s3"))
    online = {"flag": False}

    def flaky_ingest(payload, snap):
        if not online["flag"]:
            raise ConnectionError("Simulated AWS network outage")
        return store.ingest_edge_event(payload, snap)

    publisher = CloudEventPublisher(CloudConfig(transport="mock"), queue, mock_ingest_fn=flaky_ingest)

    try:
        evt1 = {
            "event_id": "offline-e1",
            "person_id": "001",
            "display_name": "Student 1",
            "camera_id": "cam_1",
            "direction": "CAM1_INWARD",
            "timestamp": "2026-11-26T09:00:00Z",
            "confidence": 0.95,
        }
        evt2 = {
            "event_id": "offline-e2",
            "person_id": "001",
            "display_name": "Student 1",
            "camera_id": "cam_1",
            "direction": "CAM1_OUTWARD",
            "timestamp": "2026-11-26T09:10:00Z",
            "confidence": 0.93,
        }

        assert publisher.publish_crossing(evt1) == 0
        assert publisher.publish_crossing(evt2) == 0
        assert queue.pending_count() == 2
        assert store.get_person_state("001") is None

        # Restore connectivity and flush
        online["flag"] = True
        flushed = publisher.flush_pending()
        assert flushed == 2
        assert queue.pending_count() == 0
        assert store.get_person_state("001").current_zone.value == "OUTSIDE"
        assert len(store.list_movement_logs()) == 2
    finally:
        queue.close()
        store.close()
