"""Unit tests for LiveStreamHub MJPEG preview and frame annotation."""

from __future__ import annotations

import cv2
import numpy as np

from backend.stream_manager import LiveStreamHub, annotate_monitor_frame


def test_live_stream_hub_preview_and_push() -> None:
    hub = LiveStreamHub()

    # Fallback preview generates valid JPEG for cam_1 and cam_2
    jpg1 = hub.get_latest_jpeg("cam_1")
    jpg2 = hub.get_latest_jpeg("cam_2")
    assert len(jpg1) > 1000
    assert len(jpg2) > 1000
    arr1 = cv2.imdecode(np.frombuffer(jpg1, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert arr1 is not None and arr1.shape[:2] == (400, 640)

    # Pushed live frame overrides preview
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
    assert hub.get_latest_jpeg("cam_1") == buf.tobytes()

    chunk = next(hub.mjpeg_generator("cam_1", fps=10.0))
    assert b"--frame\r\nContent-Type: image/jpeg" in chunk
