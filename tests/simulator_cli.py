"""Local Dual-Video Simulator CLI for testing VEXDET ONNX, LineZones, ReID, and Away Timers offline."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
LAMBDAS_DIR = ROOT / "aws" / "lambdas"
for p in (str(ROOT), str(LAMBDAS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from edge.src.config import SystemConfig, load_system_config
from edge.src.detector import DetectionBatch, PersonDetectorBackend, VexdetOnnxDetector
from edge.src.edge_runner import EdgePipelineProcessor
from edge.src.offline_queue import OfflineEventQueue
from edge.src.publisher import CloudEventPublisher
from edge.src.reid import CrossingReIDMatcher
from edge.src.vision_utils import compute_blur_score, deduplicate_detections
from tests.mock_aws_store import MockLocalStateStore


class SyntheticAvatarDetector:
    """Color-blob person detector used ONLY for synthetic dual-MP4 offline tests when ONNX weights are absent."""

    def __init__(self, target_hsv_ranges: Optional[List[Tuple[int, int]]] = None) -> None:
        # Detects distinctly colored synthetic student/bystander rectangles against dark room floor
        self._ranges = target_hsv_ranges or [(0, 180)]

    def detect(self, frame_bgr: np.ndarray) -> DetectionBatch:
        blur = compute_blur_score(frame_bgr)
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        # Foreground mask: high saturation/brightness figures over neutral gray floor
        mask = cv2.inRange(hsv, np.array([0, 90, 80]), np.array([180, 255, 255]))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        boxes: List[List[float]] = []
        scores: List[float] = []
        cids: List[int] = []
        names: List[str] = []

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w < 18 or h < 40:
                continue
            boxes.append([float(x), float(y), float(x + w), float(y + h)])
            scores.append(0.92)
            cids.append(0)
            names.append("person")

        if not boxes:
            return DetectionBatch(
                boxes_xyxy=np.empty((0, 4), dtype=np.float32),
                scores=np.empty((0,), dtype=np.float32),
                class_ids=np.empty((0,), dtype=np.int32),
                class_names=[],
                blur_score=blur,
            )

        b_arr, s_arr, c_arr, n_list = deduplicate_detections(
            np.asarray(boxes, dtype=np.float32),
            np.asarray(scores, dtype=np.float32),
            np.asarray(cids, dtype=np.int32),
            names,
            iou_threshold=0.5,
        )
        return DetectionBatch(
            boxes_xyxy=b_arr,
            scores=s_arr,
            class_ids=c_arr,
            class_names=n_list,
            blur_score=blur,
        )


def _draw_person_sprite(
    frame: np.ndarray,
    cx: int,
    foot_y: int,
    shirt_bgr: Tuple[int, int, int],
    pants_bgr: Tuple[int, int, int],
) -> None:
    """Render a crisp textured single-contour person silhouette for deterministic synthetic tests."""
    w, h = 44, 108
    x1 = max(0, cx - w // 2)
    x2 = min(frame.shape[1], cx + w // 2)
    y1 = max(0, foot_y - h)
    y2 = min(frame.shape[0], foot_y)
    if x2 <= x1 or y2 <= y1:
        return
    # Head (warm peach tone with S > 95 so contour remains one connected component)
    cv2.rectangle(frame, (x1 + 10, y1), (x2 - 10, y1 + 22), (80, 145, 225), -1)
    # Shirt (upper torso)
    cv2.rectangle(frame, (x1, y1 + 22), (x2, y1 + 65), shirt_bgr, -1)
    # Pants (lower body)
    cv2.rectangle(frame, (x1 + 4, y1 + 65), (x2 - 4, y2), pants_bgr, -1)
    # Interior textured accents (kept inside torso/legs without breaking outer contour)
    cv2.line(frame, (x1 + 8, y1 + 32), (x2 - 8, y1 + 32), (245, 245, 245), 2)
    cv2.line(frame, (x1 + 10, y1 + 82), (x2 - 10, y1 + 82), (245, 245, 245), 2)


def generate_synthetic_scenario_videos(output_dir: Path) -> Dict[str, Any]:
    """Generate synchronized dual-room MP4s + student reference crops for deterministic offline E2E testing.

    Timeline (at 10 FPS = 0.1s per frame, total 250 frames = 25.0s):
      - 0.0s .. 3.0s  : Student 1 (Blue/Red) walks INWARD across Cam 1 (OUTSIDE -> IN_ROOM_1)
      - 3.5s .. 6.5s  : Bystander (Green/Yellow) walks INWARD across Cam 1 (must be IGNORED in strict mode)
      - 7.0s .. 10.0s : Student 1 crosses Cam 2 INWARD into Room 2 (IN_ROOM_1 -> IN_ROOM_2, NO away timer!)
      - 11.5s .. 14.5s: Student 1 crosses Cam 2 OUTWARD back to Room 1 (IN_ROOM_2 -> IN_ROOM_1, NO away timer!)
      - 15.0s .. 17.5s: Student 1 crosses Cam 1 OUTWARD to OUTSIDE (STARTS Away Timer)
      - 20.5s .. 23.0s: Student 1 returns INWARD across Cam 1 (STOPS Away Timer, away_duration ~ 5s)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    cam1_path = output_dir / "sim_cam1.mp4"
    cam2_path = output_dir / "sim_cam2.mp4"

    fps = 10.0
    w, h = 640, 480
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    w1 = cv2.VideoWriter(str(cam1_path), fourcc, fps, (w, h))
    w2 = cv2.VideoWriter(str(cam2_path), fourcc, fps, (w, h))

    # Reference crops for Student 1 (Blue shirt, Red pants) and Student 2 (Purple shirt, Teal pants)
    s1_shirt, s1_pants = (230, 60, 20), (30, 40, 210)
    s2_shirt, s2_pants = (210, 30, 180), (190, 180, 20)
    bystander_shirt, bystander_pants = (30, 210, 40), (20, 220, 220)

    def make_ref_crop(shirt, pants) -> np.ndarray:
        canvas = np.full((140, 70, 3), 55, dtype=np.uint8)
        _draw_person_sprite(canvas, 35, 125, shirt, pants)
        # Extract tight bounding box matching SyntheticAvatarDetector output
        return canvas[17:125, 13:57].copy()

    s1_ref = make_ref_crop(s1_shirt, s1_pants)
    s2_ref = make_ref_crop(s2_shirt, s2_pants)

    total_frames = 250
    for idx in range(total_frames):
        f1 = np.full((h, w, 3), 55, dtype=np.uint8)
        f2 = np.full((h, w, 3), 50, dtype=np.uint8)
        cv2.line(f1, (0, int(0.55 * h)), (w, int(0.55 * h)), (95, 95, 95), 1)
        cv2.line(f2, (0, int(0.45 * h)), (w, int(0.45 * h)), (95, 95, 95), 1)

        # 0..30: Student 1 enters Cam 1 (y: 190 -> 360 across y=264)
        if 0 <= idx <= 30:
            y = int(190 + (idx / 30.0) * 170)
            _draw_person_sprite(f1, 240, y, s1_shirt, s1_pants)

        # 35..65: Bystander enters Cam 1 (y: 190 -> 360)
        if 35 <= idx <= 65:
            y = int(190 + ((idx - 35) / 30.0) * 170)
            _draw_person_sprite(f1, 410, y, bystander_shirt, bystander_pants)

        # 70..100: Student 1 enters Room 2 via Cam 2 (y: 150 -> 320 across y=216)
        if 70 <= idx <= 100:
            y = int(150 + ((idx - 70) / 30.0) * 170)
            _draw_person_sprite(f2, 320, y, s1_shirt, s1_pants)

        # 115..145: Student 1 exits Room 2 via Cam 2 (y: 320 -> 150)
        if 115 <= idx <= 145:
            y = int(320 - ((idx - 115) / 30.0) * 170)
            _draw_person_sprite(f2, 320, y, s1_shirt, s1_pants)

        # 150..175: Student 1 exits Campus via Cam 1 (y: 350 -> 185)
        if 150 <= idx <= 175:
            y = int(350 - ((idx - 150) / 25.0) * 165)
            _draw_person_sprite(f1, 240, y, s1_shirt, s1_pants)

        # 205..230: Student 1 returns to Campus via Cam 1 (y: 185 -> 355)
        if 205 <= idx <= 230:
            y = int(185 + ((idx - 205) / 25.0) * 170)
            _draw_person_sprite(f1, 240, y, s1_shirt, s1_pants)

        w1.write(f1)
        w2.write(f2)

    w1.release()
    w2.release()

    return {
        "cam1_path": str(cam1_path),
        "cam2_path": str(cam2_path),
        "student_references": {
            "001": ("Student 1", s1_ref),
            "002": ("Student 2", s2_ref),
        },
    }


def run_simulation(
    cam1_video: Optional[str] = None,
    cam2_video: Optional[str] = None,
    synthetic: bool = False,
    work_dir: Path = Path("edge/data/sim_run"),
    vexdet_onnx_path: Optional[str] = None,
    strict_target_mode: bool = True,
    away_alert_threshold_seconds: float = 900.0,
) -> Dict[str, Any]:
    """Run two MP4 videos through the full Edge + ReID + Mock AWS state engine pipeline."""
    work_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_system_config()
    cfg.reid.strict_target_mode = strict_target_mode
    cfg.reid.combined_match_threshold = 0.55
    cfg.reid.match_margin_threshold = 0.05
    cfg.cloud.transport = "mock"
    cfg.cloud.offline_db_path = str(work_dir / "outbox.db")
    cfg.cloud.snapshots_dir = str(work_dir / "snapshots")
    cfg.cloud.local_state_db_path = str(work_dir / "local_state.db")
    cfg.cloud.away_alert_threshold_seconds = away_alert_threshold_seconds

    student_refs: Dict[str, Tuple[str, np.ndarray]] = {}
    if synthetic or (not cam1_video and not cam2_video):
        sim_assets = generate_synthetic_scenario_videos(work_dir / "videos")
        cam1_video = sim_assets["cam1_path"]
        cam2_video = sim_assets["cam2_path"]
        student_refs = sim_assets["student_references"]

    if not cam1_video or not cam2_video:
        raise ValueError("Both --cam1 and --cam2 MP4 paths are required unless --synthetic is set.")

    detector: PersonDetectorBackend
    if vexdet_onnx_path and Path(vexdet_onnx_path).is_file():
        cfg.vexdet.onnx_path = vexdet_onnx_path
        detector = VexdetOnnxDetector(cfg.vexdet)
    else:
        detector = SyntheticAvatarDetector()

    store = MockLocalStateStore(
        db_path=cfg.cloud.local_state_db_path,
        snapshots_dir=str(work_dir / "mock_s3"),
        away_alert_threshold_seconds=away_alert_threshold_seconds,
    )
    queue = OfflineEventQueue(cfg.cloud.offline_db_path)
    publisher = CloudEventPublisher(
        config=cfg.cloud,
        queue=queue,
        mock_ingest_fn=store.ingest_edge_event,
    )
    reid = CrossingReIDMatcher(config=cfg.reid, custom_class_map=cfg.vexdet.custom_class_map)
    for pid, (disp, ref_bgr) in student_refs.items():
        reid.register_student_reference(pid, disp, ref_bgr)

    processor = EdgePipelineProcessor(
        config=cfg,
        detector=detector,
        publisher=publisher,
        reid_matcher=reid,
    )

    try:
        base_epoch = datetime(2026, 11, 26, 10, 0, 0, tzinfo=timezone.utc).timestamp()
        emitted = processor.run_dual_video_files(cam1_video, cam2_video, base_epoch=base_epoch)
        states = [s.model_dump() for s in store.list_person_states()]
        logs = [l.model_dump() for l in store.list_movement_logs(limit=200)]
        alerts = list(store.sns_client.published_messages)
        pending_outbox = queue.pending_count()
    finally:
        queue.close()
        store.close()

    return {
        "emitted_count": len(emitted),
        "states": states,
        "logs": logs,
        "sns_alerts": alerts,
        "pending_outbox": pending_outbox,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="WorkZone Vision Local Dual-Video Simulator")
    parser.add_argument("--cam1", help="Path to Room 1 Main Door MP4 video")
    parser.add_argument("--cam2", help="Path to Room 2 Connecting Door MP4 video")
    parser.add_argument("--synthetic", action="store_true", help="Generate and run synthetic dual-room scenario")
    parser.add_argument("--vexdet-onnx", help="Optional path to exported VEXDET ONNX model")
    parser.add_argument("--work-dir", default="edge/data/sim_run")
    parser.add_argument("--all-persons", action="store_true", help="Disable strict 2-student filtering")
    parser.add_argument("--away-threshold", type=float, default=900.0)
    args = parser.parse_args()

    result = run_simulation(
        cam1_video=args.cam1,
        cam2_video=args.cam2,
        synthetic=args.synthetic or (not args.cam1 and not args.cam2),
        work_dir=Path(args.work_dir),
        vexdet_onnx_path=args.vexdet_onnx,
        strict_target_mode=not args.all_persons,
        away_alert_threshold_seconds=args.away_threshold,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
