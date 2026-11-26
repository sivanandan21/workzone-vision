"""Independent per-camera ByteTrack tracker and sharpest-crop buffer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.optimize import linear_sum_assignment

from edge.src.detector import DetectionBatch
from edge.src.vision_utils import compute_blur_score, compute_iou_matrix


@dataclass
class TrackedObject:
    camera_id: str
    track_id: int
    box_xyxy: np.ndarray
    foot_point: Tuple[float, float]
    confidence: float
    class_name: str
    sharpest_crop_bgr: Optional[np.ndarray] = None
    sharpest_blur_score: float = 0.0


@dataclass
class _TrackState:
    track_id: int
    box_xyxy: np.ndarray
    velocity: np.ndarray  # [vx, vy, vw, vh]
    confidence: float
    class_name: str
    hits: int = 1
    misses: int = 0


class _BuiltinByteTracker:
    """Two-stage (high/low confidence) Kalman+IoU ByteTrack fallback."""

    def __init__(
        self,
        high_thresh: float = 0.40,
        low_thresh: float = 0.15,
        iou_thresh: float = 0.25,
        max_lost_frames: int = 30,
    ) -> None:
        self.high_thresh = high_thresh
        self.low_thresh = low_thresh
        self.iou_thresh = iou_thresh
        self.max_lost_frames = max_lost_frames
        self._next_id = 1
        self._tracks: List[_TrackState] = []

    @staticmethod
    def _predict_box(track: _TrackState) -> np.ndarray:
        cx = (track.box_xyxy[0] + track.box_xyxy[2]) * 0.5 + track.velocity[0]
        cy = (track.box_xyxy[1] + track.box_xyxy[3]) * 0.5 + track.velocity[1]
        bw = max(4.0, (track.box_xyxy[2] - track.box_xyxy[0]) + track.velocity[2])
        bh = max(8.0, (track.box_xyxy[3] - track.box_xyxy[1]) + track.velocity[3])
        return np.array([cx - bw * 0.5, cy - bh * 0.5, cx + bw * 0.5, cy + bh * 0.5], dtype=np.float32)

    @staticmethod
    def _update_track(track: _TrackState, new_box: np.ndarray, conf: float, cname: str) -> None:
        old_cx = (track.box_xyxy[0] + track.box_xyxy[2]) * 0.5
        old_cy = (track.box_xyxy[1] + track.box_xyxy[3]) * 0.5
        old_w = track.box_xyxy[2] - track.box_xyxy[0]
        old_h = track.box_xyxy[3] - track.box_xyxy[1]

        new_cx = (new_box[0] + new_box[2]) * 0.5
        new_cy = (new_box[1] + new_box[3]) * 0.5
        new_w = new_box[2] - new_box[0]
        new_h = new_box[3] - new_box[1]

        measured_v = np.array([new_cx - old_cx, new_cy - old_cy, new_w - old_w, new_h - old_h], dtype=np.float32)
        track.velocity = 0.6 * track.velocity + 0.4 * measured_v
        track.box_xyxy = new_box.astype(np.float32)
        track.confidence = float(conf)
        track.class_name = cname
        track.hits += 1
        track.misses = 0

    def _associate(
        self,
        track_indices: List[int],
        det_indices: List[int],
        pred_boxes: np.ndarray,
        det_boxes: np.ndarray,
    ) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
        if not track_indices or not det_indices:
            return [], track_indices, det_indices

        t_boxes = pred_boxes[track_indices]
        d_boxes = det_boxes[det_indices]
        ious = compute_iou_matrix(t_boxes, d_boxes)
        row_ind, col_ind = linear_sum_assignment(-ious)

        matched: List[Tuple[int, int]] = []
        matched_t = set()
        matched_d = set()
        for r, c in zip(row_ind, col_ind):
            if ious[r, c] >= self.iou_thresh:
                t_idx = track_indices[r]
                d_idx = det_indices[c]
                matched.append((t_idx, d_idx))
                matched_t.add(t_idx)
                matched_d.add(d_idx)

        unmatched_t = [t for t in track_indices if t not in matched_t]
        unmatched_d = [d for d in det_indices if d not in matched_d]
        return matched, unmatched_t, unmatched_d

    def update(self, batch: DetectionBatch) -> List[Tuple[int, np.ndarray, float, str]]:
        pred_boxes = (
            np.stack([self._predict_box(t) for t in self._tracks], axis=0)
            if self._tracks
            else np.empty((0, 4), dtype=np.float32)
        )

        high_dets = [i for i, s in enumerate(batch.scores) if s >= self.high_thresh]
        low_dets = [i for i, s in enumerate(batch.scores) if self.low_thresh <= s < self.high_thresh]

        all_tracks = list(range(len(self._tracks)))
        matched_1, unmatched_tracks, unmatched_high = self._associate(
            all_tracks, high_dets, pred_boxes, batch.boxes_xyxy
        )
        for t_idx, d_idx in matched_1:
            self._update_track(
                self._tracks[t_idx],
                batch.boxes_xyxy[d_idx],
                float(batch.scores[d_idx]),
                batch.class_names[d_idx],
            )

        matched_2, still_unmatched_tracks, _ = self._associate(
            unmatched_tracks, low_dets, pred_boxes, batch.boxes_xyxy
        )
        for t_idx, d_idx in matched_2:
            self._update_track(
                self._tracks[t_idx],
                batch.boxes_xyxy[d_idx],
                float(batch.scores[d_idx]),
                batch.class_names[d_idx],
            )

        for t_idx in still_unmatched_tracks:
            self._tracks[t_idx].misses += 1
            self._tracks[t_idx].box_xyxy = pred_boxes[t_idx]

        # Spawn new tracks from remaining high-confidence detections
        active_results: List[Tuple[int, np.ndarray, float, str]] = []
        matched_t_indices = {t_idx for t_idx, _ in matched_1 + matched_2}
        for t_idx in matched_t_indices:
            t = self._tracks[t_idx]
            active_results.append((t.track_id, t.box_xyxy.copy(), t.confidence, t.class_name))

        for d_idx in unmatched_high:
            new_t = _TrackState(
                track_id=self._next_id,
                box_xyxy=batch.boxes_xyxy[d_idx].astype(np.float32),
                velocity=np.zeros(4, dtype=np.float32),
                confidence=float(batch.scores[d_idx]),
                class_name=batch.class_names[d_idx],
            )
            self._next_id += 1
            self._tracks.append(new_t)
            active_results.append((new_t.track_id, new_t.box_xyxy.copy(), new_t.confidence, new_t.class_name))

        # Prune expired lost tracks
        self._tracks = [t for t in self._tracks if t.misses <= self.max_lost_frames]
        return active_results


class IndependentCameraTracker:
    """Single-camera ByteTrack wrapper + rolling sharpest-crop memory per track_id."""

    def __init__(self, camera_id: str, min_crop_width: int = 12, min_crop_height: int = 20) -> None:
        self.camera_id = camera_id
        self.min_crop_width = min_crop_width
        self.min_crop_height = min_crop_height
        self._sv_tracker = None
        self._builtin_tracker: Optional[_BuiltinByteTracker] = None
        self._best_crops: Dict[int, Tuple[float, np.ndarray]] = {}

        try:
            import supervision as sv
            self._sv_tracker = sv.ByteTrack()
        except ImportError:
            self._builtin_tracker = _BuiltinByteTracker()

    def update(self, frame_bgr: np.ndarray, detections: DetectionBatch) -> List[TrackedObject]:
        """Update tracker with detections from a single camera and return tracked objects."""
        h, w = frame_bgr.shape[:2]
        tracked_entries: List[Tuple[int, np.ndarray, float, str]] = []

        if self._sv_tracker is not None:
            sv_dets = detections.to_supervision()
            sv_tracked = self._sv_tracker.update_with_detections(sv_dets)
            if len(sv_tracked) > 0 and sv_tracked.tracker_id is not None:
                cnames = (sv_tracked.data or {}).get("class_name", ["person"] * len(sv_tracked))
                for i in range(len(sv_tracked)):
                    tid = int(sv_tracked.tracker_id[i])
                    box = np.asarray(sv_tracked.xyxy[i], dtype=np.float32)
                    conf = float(sv_tracked.confidence[i]) if sv_tracked.confidence is not None else 1.0
                    cname = str(cnames[i])
                    tracked_entries.append((tid, box, conf, cname))
        else:
            tracked_entries = self._builtin_tracker.update(detections)

        results: List[TrackedObject] = []
        for tid, box, conf, cname in tracked_entries:
            x1 = int(np.clip(box[0], 0, w - 1))
            y1 = int(np.clip(box[1], 0, h - 1))
            x2 = int(np.clip(box[2], 0, w))
            y2 = int(np.clip(box[3], 0, h))
            foot = (float((box[0] + box[2]) * 0.5), float(box[3]))

            if (x2 - x1) >= self.min_crop_width and (y2 - y1) >= self.min_crop_height:
                crop = frame_bgr[y1:y2, x1:x2].copy()
                blur = compute_blur_score(crop)
                prev_best = self._best_crops.get(tid)
                if prev_best is None or blur >= prev_best[0]:
                    self._best_crops[tid] = (blur, crop)

            best_blur, best_crop = self._best_crops.get(tid, (0.0, None))
            results.append(
                TrackedObject(
                    camera_id=self.camera_id,
                    track_id=tid,
                    box_xyxy=box,
                    foot_point=foot,
                    confidence=conf,
                    class_name=cname,
                    sharpest_crop_bgr=best_crop,
                    sharpest_blur_score=best_blur,
                )
            )
        return results
