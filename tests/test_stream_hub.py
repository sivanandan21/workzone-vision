"""Unit tests for LiveStreamHub empty standby frame, live status tracking, and MJPEG stream."""

from __future__ import annotations

import cv2
import numpy as np

from backend.stream_manager import LiveStreamHub
from edge.src.vision_utils import annotate_monitor_frame


def test_live_stream_hub_standby_and_push() -> None:
    hub = LiveStreamHub(stale_after_seconds=5.0)

    # Initial status must report no live camera connected
    status_before = hub.get_status()
    assert status_before["cam_1"]["live"] is False
    assert status_before["cam_2"]["live"] is False

    # Standby frame is clean dark canvas (no fake green detection boxes)
    jpg1 = hub.get_latest_jpeg("cam_1")
    arr1 = cv2.imdecode(np.frombuffer(jpg1, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert arr1 is not None and arr1.shape[:2] == (400, 640)
    assert float(np.mean(arr1)) < 45.0

    # Pushed live frame turns cam_1 live and overrides standby frame
    custom = np.full((240, 320, 3), 90, dtype=np.uint8)
    annotated = annotate_monitor_frame(
        custom,
        boxes_with_labels=[((40.0, 30.0, 120.0, 200.0), "S-001")],
        tripwire_segment=((20, 140), (300, 140)),
        tripwire_label="tripwire",
    )
    ok, buf = cv2.imencode(".jpg", annotated)
    assert ok
    hub.push_jpeg("cam_1", buf.tobytes())

    status_after = hub.get_status()
    assert status_after["cam_1"]["live"] is True
    assert status_after["cam_1"]["frames_received"] == 1
    assert status_after["cam_2"]["live"] is False
    assert hub.get_latest_jpeg("cam_1") == buf.tobytes()

    chunk = next(hub.mjpeg_generator("cam_1", fps=10.0))
    assert b"--frame\r\nContent-Type: image/jpeg" in chunk
