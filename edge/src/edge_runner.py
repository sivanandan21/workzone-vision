"""Main edge orchestrator wiring Cam 1 & Cam 2 streams, VEXDET ONNX, ByteTrack, LineZones, and ReID."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import uuid
import cv2
import numpy as np

from edge.src.capture import FramePacket, LatestFrameStream, SequentialVideoReader
from edge.src.config import SystemConfig
from edge.src.detector import PersonDetectorBackend, VexdetOnnxDetector
from edge.src.publisher import CloudEventPublisher
from edge.src.reid import CrossingReIDMatcher
from edge.src.tracker import IndependentCameraTracker
from edge.src.tripwire import BidirectionalLineZone, CrossingEvent

logger = logging.getLogger("workzone.edge")


class EdgePipelineProcessor:
    """Processes synchronized or live frames from cam_1 and cam_2 and emits crossing payloads."""

    def __init__(
        self,
        config: SystemConfig,
        detector: PersonDetectorBackend,
        publisher: CloudEventPublisher,
        reid_matcher: Optional[CrossingReIDMatcher] = None,
    ) -> None:
        self.config = config
        self.detector = detector
        self.publisher = publisher
        self.reid = reid_matcher or CrossingReIDMatcher(
            config=config.reid,
            custom_class_map=config.vexdet.custom_class_map,
        )

        self.trackers: Dict[str, IndependentCameraTracker] = {
            "cam_1": IndependentCameraTracker("cam_1"),
            "cam_2": IndependentCameraTracker("cam_2"),
        }
        self.tripwires: Dict[str, BidirectionalLineZone] = {
            cam_id: BidirectionalLineZone(cam_id, cam_cfg.tripwire)
            for cam_id, cam_cfg in config.cameras.items()
        }
        self.snapshots_dir = Path(config.cloud.snapshots_dir)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.emitted_events: List[Dict[str, Any]] = []
        self._last_preview_push: Dict[str, float] = {}

    def process_frame(self, packet: FramePacket, iso_timestamp_override: Optional[str] = None) -> List[Dict[str, Any]]:
        """Process one camera frame packet; returns any emitted crossing payloads."""
        cam_id = packet.camera_id
        frame_bgr = packet.frame_bgr
        h, w = frame_bgr.shape[:2]

        batch = self.detector.detect(frame_bgr)
        tracked = self.trackers[cam_id].update(frame_bgr, batch)
        crossings = self.tripwires[cam_id].update(tracked, w, h, packet.timestamp_epoch)

        emitted: List[Dict[str, Any]] = []
        for crossing in crossings:
            payload = self._handle_crossing(crossing, iso_timestamp_override)
            if payload is not None:
                emitted.append(payload)
                self.emitted_events.append(payload)

        # Periodically retry any buffered offline events
        if not crossings and self.publisher.queue.pending_count() > 0:
            self.publisher.flush_pending()

        self._maybe_push_live_preview(cam_id, frame_bgr, tracked, w, h)
        return emitted

    def _maybe_push_live_preview(
        self,
        cam_id: str,
        frame_bgr: np.ndarray,
        tracked: list,
        frame_w: int,
        frame_h: int,
    ) -> None:
        """Best-effort push of annotated live frame to EC2 backend MJPEG hub when transport=https."""
        if self.config.cloud.transport.lower() != "https" or not self.config.cloud.api_endpoint:
            return
        now = time.time()
        if (now - self._last_preview_push.get(cam_id, 0.0)) < 0.12:
            return
        self._last_preview_push[cam_id] = now
        try:
            import urllib.request
            from edge.src.vision_utils import annotate_monitor_frame

            boxes_labels = []
            for obj in tracked:
                key = (cam_id, obj.track_id)
                pid_tuple = self.reid._track_to_person.get(key)
                label = f"S-{pid_tuple[0]}" if pid_tuple else f"T-{obj.track_id}"
                boxes_labels.append((tuple(obj.box_xyxy.tolist()), label))

            tripwire_seg = None
            tw_cfg = self.config.cameras[cam_id].tripwire
            if cam_id == "cam_1":
                if tw_cfg.normalized:
                    p1 = (int(tw_cfg.p1[0] * frame_w), int(tw_cfg.p1[1] * frame_h))
                    p2 = (int(tw_cfg.p2[0] * frame_w), int(tw_cfg.p2[1] * frame_h))
                else:
                    p1 = (int(tw_cfg.p1[0]), int(tw_cfg.p1[1]))
                    p2 = (int(tw_cfg.p2[0]), int(tw_cfg.p2[1]))
                tripwire_seg = (p1, p2)

            annotated = annotate_monitor_frame(
                frame_bgr,
                boxes_with_labels=boxes_labels,
                tripwire_segment=tripwire_seg,
                tripwire_label="tripwire" if tripwire_seg else "",
            )
            ok, buf = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if not ok:
                return
            url = f"{self.config.cloud.api_endpoint.rstrip('/')}/ingest/frame/{cam_id}"
            req = urllib.request.Request(
                url,
                data=buf.tobytes(),
                headers={
                    "Content-Type": "image/jpeg",
                    "X-Edge-Api-Key": self.config.cloud.api_key,
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2.0).close()
        except Exception as exc:
            logger.debug("[%s] Preview frame push error: %s", cam_id, exc)

    def _handle_crossing(
        self,
        crossing: CrossingEvent,
        iso_timestamp_override: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run ReID ONLY on crossing events, save sharp crop, and publish via SQLite outbox."""
        match = self.reid.match_crossing(
            camera_id=crossing.camera_id,
            tracked_object=crossing.tracked_object,
            direction=crossing.direction,
            timestamp_epoch=crossing.timestamp_epoch,
        )
        if not match.matched or match.person_id is None:
            return None

        event_id = str(uuid.uuid4())
        cam_prefix = crossing.camera_id.replace("_", "").upper()
        direction_code = f"{cam_prefix}_{crossing.direction}"
        ts_iso = iso_timestamp_override or datetime.fromtimestamp(
            crossing.timestamp_epoch, tz=timezone.utc
        ).isoformat().replace("+00:00", "Z")

        snapshot_local_path: Optional[str] = None
        snapshot_s3_key: Optional[str] = None
        crop = crossing.tracked_object.sharpest_crop_bgr
        if crop is not None and crop.size > 0:
            fname = f"{match.person_id}_{crossing.camera_id}_{event_id[:8]}.jpg"
            local_file = self.snapshots_dir / fname
            cv2.imwrite(str(local_file), crop)
            snapshot_local_path = str(local_file)
            snapshot_s3_key = f"snapshots/{fname}"

        payload = {
            "event_id": event_id,
            "person_id": match.person_id,
            "display_name": match.display_name,
            "camera_id": crossing.camera_id,
            "direction": direction_code,
            "timestamp": ts_iso,
            "confidence": round(float(match.confidence), 4),
            "local_track_id": crossing.track_id,
            "snapshot_s3_key": snapshot_s3_key,
            "blur_score": round(float(crossing.tracked_object.sharpest_blur_score), 2),
            "reid_method": match.method,
        }
        self.publisher.publish_crossing(payload, snapshot_local_path=snapshot_local_path)
        return payload

    def run_dual_video_files(
        self,
        cam1_video_path: str,
        cam2_video_path: str,
        base_epoch: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Process two local MP4 video files sequentially (interleaved by timestamp)."""
        r1 = SequentialVideoReader("cam_1", cam1_video_path)
        r2 = SequentialVideoReader("cam_2", cam2_video_path)
        start_epoch = base_epoch if base_epoch is not None else time.time()

        try:
            p1 = r1.read_next()
            p2 = r2.read_next()
            while p1 is not None or p2 is not None:
                if p2 is None or (p1 is not None and p1.timestamp_epoch <= p2.timestamp_epoch):
                    pkt = p1
                    p1 = r1.read_next()
                else:
                    pkt = p2
                    p2 = r2.read_next()

                real_epoch = start_epoch + pkt.timestamp_epoch
                iso = datetime.fromtimestamp(real_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
                pkt.timestamp_epoch = real_epoch
                self.process_frame(pkt, iso_timestamp_override=iso)
        finally:
            r1.release()
            r2.release()
            self.publisher.flush_pending()

        return list(self.emitted_events)

    def run_live_rtsp(self, stop_after_seconds: Optional[float] = None) -> None:
        """Run multi-threaded RTSP latest-frame processing loop for cam_1 and cam_2."""
        cam1_cfg = self.config.cameras["cam_1"]
        cam2_cfg = self.config.cameras["cam_2"]
        s1 = LatestFrameStream("cam_1", cam1_cfg.source).start()
        s2 = LatestFrameStream("cam_2", cam2_cfg.source).start()
        seq_seen = {"cam_1": -1, "cam_2": -1}
        last_proc = {"cam_1": 0.0, "cam_2": 0.0}
        min_interval = {
            "cam_1": 1.0 / max(cam1_cfg.target_fps, 0.5),
            "cam_2": 1.0 / max(cam2_cfg.target_fps, 0.5),
        }
        t_start = time.time()

        try:
            while True:
                if stop_after_seconds is not None and (time.time() - t_start) >= stop_after_seconds:
                    break
                progressed = False
                now = time.time()
                for cid, stream in (("cam_1", s1), ("cam_2", s2)):
                    if (now - last_proc[cid]) < min_interval[cid]:
                        continue
                    pkt = stream.read_latest(seq_seen[cid])
                    if pkt is not None:
                        seq_seen[cid] = pkt.frame_seq
                        last_proc[cid] = now
                        self.process_frame(pkt)
                        progressed = True
                if not progressed:
                    time.sleep(0.015)
        finally:
            s1.stop()
            s2.stop()
            self.publisher.flush_pending()


def test_camera_connections(cfg: SystemConfig, push_preview: bool = True) -> bool:
    """Quick preflight check verifying RTSP/webcam frame grab & optional backend preview push."""
    all_ok = True
    for cid in ("cam_1", "cam_2"):
        cam_cfg = cfg.cameras[cid]
        src: Any = int(cam_cfg.source) if cam_cfg.source.isdigit() else cam_cfg.source
        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG) if isinstance(src, str) else cv2.VideoCapture(src)
        ret, frame = cap.read() if cap.isOpened() else (False, None)
        cap.release()
        if not ret or frame is None:
            logger.error("[%s] FAILED to read frame from '%s'", cid, cam_cfg.source)
            all_ok = False
            continue
        h, w = frame.shape[:2]
        logger.info("[%s] OK: read %dx%d frame from '%s'", cid, w, h, cam_cfg.source)
        if push_preview and cfg.cloud.transport.lower() == "https" and cfg.cloud.api_endpoint:
            import urllib.request
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok:
                url = f"{cfg.cloud.api_endpoint.rstrip('/')}/ingest/frame/{cid}"
                req = urllib.request.Request(
                    url,
                    data=buf.tobytes(),
                    headers={"Content-Type": "image/jpeg", "X-Edge-Api-Key": cfg.cloud.api_key},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=3.0).close()
                logger.info("[%s] Pushed test frame to %s", cid, url)
    return all_ok


def run_edge_service(
    config_path: Optional[str] = None,
    cam1_source: Optional[str] = None,
    cam2_source: Optional[str] = None,
    api_url: Optional[str] = None,
    api_key: Optional[str] = None,
    onnx_path: Optional[str] = None,
    test_only: bool = False,
) -> None:
    from edge.src.config import load_system_config
    from edge.src.offline_queue import OfflineEventQueue

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    cfg = load_system_config(config_path)
    if cam1_source:
        cfg.cameras["cam_1"].source = cam1_source
    if cam2_source:
        cfg.cameras["cam_2"].source = cam2_source
    if api_url:
        cfg.cloud.transport = "https"
        cfg.cloud.api_endpoint = api_url
    if api_key:
        cfg.cloud.api_key = api_key
    if onnx_path:
        cfg.vexdet.onnx_path = onnx_path

    if test_only:
        ok = test_camera_connections(cfg)
        raise SystemExit(0 if ok else 1)

    logger.info("Starting WorkZone Edge with VEXDET ONNX: %s", cfg.vexdet.onnx_path)
    logger.info("Cam 1 source: %s | Cam 2 source: %s", cfg.cameras["cam_1"].source, cfg.cameras["cam_2"].source)
    logger.info("Cloud transport: %s (%s)", cfg.cloud.transport, cfg.cloud.api_endpoint)

    detector = VexdetOnnxDetector(cfg.vexdet)
    queue = OfflineEventQueue(cfg.cloud.offline_db_path)
    publisher = CloudEventPublisher(cfg.cloud, queue)
    processor = EdgePipelineProcessor(cfg, detector, publisher)
    processor.run_live_rtsp()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WorkZone Vision Real CCTV Edge Runner")
    parser.add_argument("--config", default=None, help="Path to system_config.yaml")
    parser.add_argument("--cam1", default=None, help="Camera 1 RTSP URL, MP4 path, or webcam index")
    parser.add_argument("--cam2", default=None, help="Camera 2 RTSP URL, MP4 path, or webcam index")
    parser.add_argument("--api-url", default=None, help="Backend base URL (e.g. http://43.204.146.167)")
    parser.add_argument("--api-key", default=None, help="EDGE_INGEST_API_KEY value")
    parser.add_argument("--onnx", default=None, help="Path to exported VEXDET ONNX model")
    parser.add_argument("--test-cameras", action="store_true", help="Test camera connection + frame push and exit")
    args = parser.parse_args()

    run_edge_service(
        config_path=args.config,
        cam1_source=args.cam1,
        cam2_source=args.cam2,
        api_url=args.api_url,
        api_key=args.api_key,
        onnx_path=args.onnx,
        test_only=args.test_cameras,
    )
