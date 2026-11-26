"""Main edge orchestrator wiring Cam 1 & Cam 2 streams, VEXDET ONNX, ByteTrack, LineZones, and ReID."""

from __future__ import annotations

from datetime import datetime, timezone
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
            urllib.request.urlopen(req, timeout=0.5).close()
        except Exception:
            pass

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
        t_start = time.time()

        try:
            while True:
                if stop_after_seconds is not None and (time.time() - t_start) >= stop_after_seconds:
                    break
                progressed = False
                for cid, stream in (("cam_1", s1), ("cam_2", s2)):
                    pkt = stream.read_latest(seq_seen[cid])
                    if pkt is not None:
                        seq_seen[cid] = pkt.frame_seq
                        self.process_frame(pkt)
                        progressed = True
                if not progressed:
                    time.sleep(0.02)
        finally:
            s1.stop()
            s2.stop()
            self.publisher.flush_pending()


def run_edge_service(config_path: Optional[str] = None) -> None:
    from edge.src.config import load_system_config
    from edge.src.offline_queue import OfflineEventQueue

    cfg = load_system_config(config_path)
    detector = VexdetOnnxDetector(cfg.vexdet)
    queue = OfflineEventQueue(cfg.cloud.offline_db_path)
    publisher = CloudEventPublisher(cfg.cloud, queue)
    processor = EdgePipelineProcessor(cfg, detector, publisher)
    processor.run_live_rtsp()


if __name__ == "__main__":
    run_edge_service()
