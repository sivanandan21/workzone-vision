"""Unit tests for bidirectional LineZone tripwire & anti-lingering debounce."""

from __future__ import annotations

import numpy as np

from edge.src.config import TripwireConfig
from edge.src.tracker import TrackedObject
from edge.src.tripwire import BidirectionalLineZone


def _track(tid: int, x: float, y: float) -> TrackedObject:
    return TrackedObject(
        camera_id="cam_1",
        track_id=tid,
        box_xyxy=np.array([x - 20, y - 90, x + 20, y], dtype=np.float32),
        foot_point=(x, y),
        confidence=0.95,
        class_name="person",
    )


def test_bidirectional_crossing_and_debounce() -> None:
    cfg = TripwireConfig(
        normalized=False,
        p1=(100.0, 240.0),
        p2=(540.0, 240.0),
        inward_normal=(0.0, 1.0),
        debounce_seconds=4.0,
        deadband_ratio=0.1,  # 10 px deadband
    )
    zone = BidirectionalLineZone("cam_1", cfg)

    # Start outside (y = 200 -> signed_dist = -40)
    assert zone.update([_track(1, 300.0, 200.0)], 640, 480, timestamp_epoch=1.0) == []
    # Inside deadband (y = 244 -> signed_dist = +4) -> no crossing yet
    assert zone.update([_track(1, 300.0, 244.0)], 640, 480, timestamp_epoch=1.5) == []
    # Crosses cleanly past deadband (y = 280 -> signed_dist = +40) -> INWARD
    c1 = zone.update([_track(1, 300.0, 280.0)], 640, 480, timestamp_epoch=2.0)
    assert len(c1) == 1
    assert c1[0].direction == "INWARD"

    # Lingering back across line within 4.0s cooldown (t=3.5s) -> MUST be debounced
    c_debounced = zone.update([_track(1, 300.0, 190.0)], 640, 480, timestamp_epoch=3.5)
    assert c_debounced == []

    # Move back inside then exit cleanly AFTER cooldown expires (t=7.0s -> t=8.0s)
    assert zone.update([_track(1, 300.0, 280.0)], 640, 480, timestamp_epoch=7.0) == []
    c2 = zone.update([_track(1, 300.0, 190.0)], 640, 480, timestamp_epoch=8.0)
    assert len(c2) == 1
    assert c2[0].direction == "OUTWARD"


def test_tracker_falls_back_when_supervision_lacks_bytetrack(monkeypatch) -> None:
    import sys
    import types
    from edge.src.detector import DetectionBatch
    from edge.src.tracker import IndependentCameraTracker

    fake_sv = types.ModuleType("supervision")
    monkeypatch.setitem(sys.modules, "supervision", fake_sv)

    tracker = IndependentCameraTracker("cam_1")
    assert tracker._sv_tracker is None
    assert tracker._builtin_tracker is not None

    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    batch = DetectionBatch(
        boxes_xyxy=np.array([[100.0, 60.0, 160.0, 200.0]], dtype=np.float32),
        scores=np.array([0.9], dtype=np.float32),
        class_ids=np.array([0], dtype=np.int32),
        class_names=["person"],
    )
    tracked = tracker.update(frame, batch)
    assert len(tracked) == 1
    assert tracked[0].track_id >= 1
