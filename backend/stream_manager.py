"""Live MJPEG stream hub and clean frame annotator (green student boxes + blue tripwire)."""

from __future__ import annotations

import math
import threading
import time
from typing import Dict, Iterator, List, Optional, Tuple
import cv2
import numpy as np

from edge.src.vision_utils import annotate_monitor_frame


def _render_preview_room_frame(camera_id: str, t_sec: float) -> np.ndarray:
    """Render clean doorway preview frame when live RTSP edge feed is not actively pushing."""
    w, h = 640, 400
    frame = np.full((h, w, 3), 38, dtype=np.uint8)
    # Subtle room floor & wall shading
    cv2.rectangle(frame, (0, 0), (w, 270), (54, 58, 66), -1)
    cv2.rectangle(frame, (0, 270), (w, h), (76, 78, 82), -1)
    cv2.line(frame, (0, 270), (w, 270), (95, 98, 105), 2)

    # Doorway frame geometry
    dx1, dy1, dx2, dy2 = 215, 45, 425, 295
    cv2.rectangle(frame, (dx1 - 12, dy1 - 10), (dx2 + 12, dy2), (90, 95, 105), -1)
    cv2.rectangle(frame, (dx1, dy1), (dx2, dy2), (175, 182, 192), -1)
    # Open wood door perspective
    cv2.rectangle(frame, (dx1 + 6, dy1 + 8), (dx1 + 64, dy2), (55, 85, 130), -1)

    # Smooth periodic walk trajectory across doorway
    phase = (t_sec * 0.45) % 2.0
    progress = phase if phase <= 1.0 else (2.0 - phase)

    if camera_id == "cam_1":
        cx = 315 + int(math.sin(t_sec * 0.7) * 18)
        foot_y = int(235 + progress * 100)
        pw, ph = 54, 145
        label = "S-001"
        shirt = (88, 118, 72)
        tripwire = ((200, 240), (440, 240))
    else:
        cx = 325 - int(math.sin(t_sec * 0.6) * 16)
        foot_y = int(245 + (1.0 - progress) * 90)
        pw, ph = 52, 140
        label = "S-002"
        shirt = (145, 145, 165)
        tripwire = None

    px1, py1, px2, py2 = cx - pw // 2, foot_y - ph, cx + pw // 2, foot_y
    # Silhouette inside room frame
    cv2.circle(frame, (cx, py1 + 16), 13, (45, 50, 65), -1)
    cv2.rectangle(frame, (px1 + 6, py1 + 30), (px2 - 6, py1 + 88), shirt, -1)
    cv2.rectangle(frame, (px1 + 10, py1 + 88), (cx - 2, py2), (35, 35, 42), -1)
    cv2.rectangle(frame, (cx + 2, py1 + 88), (px2 - 10, py2), (35, 35, 42), -1)

    return annotate_monitor_frame(
        frame,
        boxes_with_labels=[((px1, py1, px2, py2), label)],
        tripwire_segment=tripwire,
        tripwire_label="tripwire" if tripwire else "",
    )


class LiveStreamHub:
    """Thread-safe MJPEG frame buffer per camera with automatic fallback preview stream."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames: Dict[str, Tuple[bytes, float, int]] = {}

    def push_jpeg(self, camera_id: str, jpeg_bytes: bytes) -> None:
        with self._lock:
            prev_seq = self._frames[camera_id][2] if camera_id in self._frames else 0
            self._frames[camera_id] = (jpeg_bytes, time.time(), prev_seq + 1)

    def get_latest_jpeg(self, camera_id: str) -> bytes:
        now = time.time()
        with self._lock:
            entry = self._frames.get(camera_id)
            if entry is not None and (now - entry[1]) < 5.0:
                return entry[0]

        # Render clean preview frame when live edge RTSP stream is not actively pushing
        bgr = _render_preview_room_frame(camera_id, now)
        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return buf.tobytes() if ok else b""

    def mjpeg_generator(self, camera_id: str, fps: float = 8.0) -> Iterator[bytes]:
        interval = 1.0 / max(fps, 1.0)
        while True:
            jpg = self.get_latest_jpeg(camera_id)
            if jpg:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpg)).encode("ascii") + b"\r\n\r\n"
                    + jpg
                    + b"\r\n"
                )
            time.sleep(interval)
