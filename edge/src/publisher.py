"""Cloud publisher supporting MockLocalStateStore, AWS API Gateway HTTPS, and AWS IoT Core MQTT."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from edge.src.config import CloudConfig
from edge.src.offline_queue import OfflineEventQueue, QueuedEvent


class CloudEventPublisher:
    """Enqueues crossing events locally first and delivers reliably to cloud/mock backend."""

    def __init__(
        self,
        config: CloudConfig,
        queue: OfflineEventQueue,
        mock_ingest_fn: Optional[Callable[[Dict[str, Any], Optional[str]], Dict[str, Any]]] = None,
    ) -> None:
        self.config = config
        self.queue = queue
        self.mock_ingest_fn = mock_ingest_fn
        self._mqtt_client = None

    def publish_crossing(self, payload: Dict[str, Any], snapshot_local_path: Optional[str] = None) -> int:
        """Persist event in SQLite outbox and attempt immediate flush of pending events."""
        self.queue.enqueue(payload["event_id"], payload, snapshot_local_path)
        return self.flush_pending()

    def flush_pending(self, batch_size: int = 25) -> int:
        """Attempt delivery of all queued pending events. Returns count flushed."""
        sent_count = 0
        for item in self.queue.peek_pending(limit=batch_size):
            try:
                self._deliver_one(item)
                self.queue.mark_sent(item.event_id)
                sent_count += 1
            except Exception as exc:
                self.queue.mark_failed(item.event_id, str(exc))
                # Stop flushing this cycle on transient network failure to preserve event order
                break
        return sent_count

    def _deliver_one(self, item: QueuedEvent) -> None:
        transport = self.config.transport.lower()
        if transport == "mock":
            if self.mock_ingest_fn is None:
                raise RuntimeError("Mock transport requires mock_ingest_fn callback.")
            self.mock_ingest_fn(item.payload, item.snapshot_local_path)
            return

        if transport == "https":
            self._deliver_https(item)
            return

        if transport == "iot_mqtt":
            self._deliver_iot_mqtt(item)
            return

        raise ValueError(f"Unsupported cloud transport '{self.config.transport}'")

    def _upload_snapshot_presigned(self, s3_key: str, local_path: str) -> None:
        import urllib.request

        presign_url = f"{self.config.api_endpoint.rstrip('/')}/ingest/presign"
        req = urllib.request.Request(
            presign_url,
            data=json.dumps({"s3_key": s3_key}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Edge-Api-Key": self.config.api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            upload_url = json.loads(resp.read().decode("utf-8"))["upload_url"]

        image_bytes = Path(local_path).read_bytes()
        put_req = urllib.request.Request(
            upload_url,
            data=image_bytes,
            headers={"Content-Type": "image/jpeg"},
            method="PUT",
        )
        with urllib.request.urlopen(put_req, timeout=15):
            pass

    def _deliver_https(self, item: QueuedEvent) -> None:
        import urllib.request

        s3_key = item.payload.get("snapshot_s3_key")
        if s3_key and item.snapshot_local_path and Path(item.snapshot_local_path).is_file():
            self._upload_snapshot_presigned(s3_key, item.snapshot_local_path)

        ingest_url = f"{self.config.api_endpoint.rstrip('/')}/ingest/crossing"
        req = urllib.request.Request(
            ingest_url,
            data=json.dumps(item.payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Edge-Api-Key": self.config.api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass

    def _deliver_iot_mqtt(self, item: QueuedEvent) -> None:
        import paho.mqtt.client as mqtt

        s3_key = item.payload.get("snapshot_s3_key")
        if s3_key and item.snapshot_local_path and Path(item.snapshot_local_path).is_file() and self.config.api_endpoint:
            self._upload_snapshot_presigned(s3_key, item.snapshot_local_path)

        if self._mqtt_client is None:
            client = mqtt.Client()
            client.tls_set(
                ca_certs=self.config.iot_ca_path,
                certfile=self.config.iot_cert_path,
                keyfile=self.config.iot_key_path,
            )
            client.connect(self.config.iot_endpoint, port=8883, keepalive=60)
            client.loop_start()
            self._mqtt_client = client

        info = self._mqtt_client.publish(self.config.iot_topic, json.dumps(item.payload), qos=1)
        info.wait_for_publish(timeout=5.0)
