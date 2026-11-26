"""Bidirectional virtual tripwire (LineZone) with per-ID debounce & deadband."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np

from edge.src.config import TripwireConfig
from edge.src.tracker import TrackedObject


@dataclass
class CrossingEvent:
    camera_id: str
    track_id: int
    direction: str  # "INWARD" or "OUTWARD"
    timestamp_epoch: float
    foot_point: Tuple[float, float]
    tracked_object: TrackedObject


class BidirectionalLineZone:
    """Detects inward and outward crossings of a doorway line with anti-lingering debounce."""

    def __init__(self, camera_id: str, config: TripwireConfig) -> None:
        self.camera_id = camera_id
        self.config = config
        self._last_stable_side: Dict[int, int] = {}
        self._last_crossing_time: Dict[int, float] = {}

        nx, ny = config.inward_normal
        norm_len = float(np.hypot(nx, ny)) or 1.0
        self._unit_normal = np.array([nx / norm_len, ny / norm_len], dtype=np.float32)

    def _resolve_segment(self, frame_w: int, frame_h: int) -> Tuple[np.ndarray, np.ndarray, float]:
        if self.config.normalized:
            p1 = np.array([self.config.p1[0] * frame_w, self.config.p1[1] * frame_h], dtype=np.float32)
            p2 = np.array([self.config.p2[0] * frame_w, self.config.p2[1] * frame_h], dtype=np.float32)
            deadband = self.config.deadband_ratio * float(min(frame_w, frame_h))
        else:
            p1 = np.array(self.config.p1, dtype=np.float32)
            p2 = np.array(self.config.p2, dtype=np.float32)
            deadband = self.config.deadband_ratio * 100.0
        return p1, p2, float(deadband)

    def _signed_side_and_projection(
        self,
        pt: Tuple[float, float],
        p1: np.ndarray,
        p2: np.ndarray,
    ) -> Tuple[float, float]:
        vec_seg = p2 - p1
        seg_len_sq = float(np.dot(vec_seg, vec_seg))
        point = np.array(pt, dtype=np.float32)
        rel = point - p1
        signed_dist = float(np.dot(rel, self._unit_normal))
        proj_t = float(np.dot(rel, vec_seg) / seg_len_sq) if seg_len_sq > 1e-6 else 0.5
        return signed_dist, proj_t

    def update(
        self,
        tracked_objects: List[TrackedObject],
        frame_w: int,
        frame_h: int,
        timestamp_epoch: float,
    ) -> List[CrossingEvent]:
        """Evaluate all tracked persons against the tripwire and emit debounced crossings."""
        p1, p2, deadband = self._resolve_segment(frame_w, frame_h)
        crossings: List[CrossingEvent] = []

        for obj in tracked_objects:
            signed_dist, proj_t = self._signed_side_and_projection(obj.foot_point, p1, p2)

            # Ignore points inside deadband jitter zone
            if abs(signed_dist) <= deadband:
                continue

            curr_side = 1 if signed_dist > 0.0 else -1
            prev_side = self._last_stable_side.get(obj.track_id)

            if prev_side is None:
                self._last_stable_side[obj.track_id] = curr_side
                continue

            if curr_side != prev_side:
                # Confirm foot lies across doorway span
                if -0.15 <= proj_t <= 1.15:
                    last_cross = self._last_crossing_time.get(obj.track_id)
                    if last_cross is not None and (timestamp_epoch - last_cross) < self.config.debounce_seconds:
                        continue
                    self._last_stable_side[obj.track_id] = curr_side
                    self._last_crossing_time[obj.track_id] = timestamp_epoch
                    # prev_side == -1 -> curr_side == +1 follows inward_normal => INWARD
                    direction = "INWARD" if (prev_side < 0 and curr_side > 0) else "OUTWARD"
                    crossings.append(
                        CrossingEvent(
                            camera_id=self.camera_id,
                            track_id=obj.track_id,
                            direction=direction,
                            timestamp_epoch=timestamp_epoch,
                            foot_point=obj.foot_point,
                            tracked_object=obj,
                        )
                    )
                else:
                    self._last_stable_side[obj.track_id] = curr_side

        return crossings
