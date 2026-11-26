"""Live MJPEG stream hub for real CCTV camera streams (with clean empty standby frame)."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Iterator, Tuple
import cv2
import numpy as np


def _render_empty_standby_frame(camera_id: str) -> np.ndarray:
    """Render clean empty dark standby frame when no real CCTV feed is connected."""
    w, h = 640, 400
    frame = np.full((h, w, 3), 24, dtype=np.uint8)
    frame[:, :] = (30, 18, 12)  # Deep dark slate BGR (#0c121e)

    # Subtle centered camera reticle
    cx, cy = w // 2, h // 2 - 12
    cv2.rectangle(frame, (cx - 28, cy - 18), (cx + 16, cy + 18), (72, 85, 110), 2, cv2.LINE_AA)
    pts = np.array([[cx + 18, cy - 8], [cx + 34, cy - 16], [cx + 34, cy + 16], [cx + 18, cy + 8]], dtype=np.int32)
    cv2.polylines(frame, [pts], isClosed=True, color=(72, 85, 110), thickness=2, lineType=cv2.LINE_AA)

    title = "Camera 1 — No Signal" if camera_id == "cam_1" else "Camera 2 — No Signal"
    subtitle = "Waiting for real CCTV RTSP / edge stream"

    (tw, _), _ = cv2.getTextSize(title, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 1)
    cv2.putText(
        frame,
        title,
        ((w - tw) // 2, cy + 48),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (165, 178, 200),
        1,
        cv2.LINE_AA,
    )
    (sw, _), _ = cv2.getTextSize(subtitle, cv2.FONT_HERSHEY_SIMPLEX, 0.44, 1)
    cv2.putText(
        frame,
        subtitle,
        ((w - sw) // 2, cy + 74),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.44,
        (105, 118, 140),
        1,
        cv2.LINE_AA,
    )
    return frame


class LiveStreamHub:
    """Thread-safe MJPEG frame buffer per camera with clean empty standby frames."""

    def __init__(self, stale_after_seconds: float = 5.0) -> None:
        self._lock = threading.Lock()
        self._stale_after_seconds = stale_after_seconds
        self._frames: Dict[str, Tuple[bytes, float, int]] = {}
        self._standby_cache: Dict[str, bytes] = {}

    def push_jpeg(self, camera_id: str, jpeg_bytes: bytes) -> None:
        with self._lock:
            prev_seq = self._frames[camera_id][2] if camera_id in self._frames else 0
            self._frames[camera_id] = (jpeg_bytes, time.time(), prev_seq + 1)

    def get_status(self) -> Dict[str, Dict[str, Any]]:
        now = time.time()
        status: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            for cid in ("cam_1", "cam_2"):
                entry = self._frames.get(cid)
                if entry is None:
                    status[cid] = {"live": False, "frames_received": 0, "last_frame_age_sec": None}
                else:
                    age = max(0.0, now - entry[1])
                    status[cid] = {
                        "live": age < self._stale_after_seconds,
                        "frames_received": entry[2],
                        "last_frame_age_sec": round(age, 2),
                    }
        return status

    def get_latest_jpeg(self, camera_id: str) -> bytes:
        now = time.time()
        with self._lock:
            entry = self._frames.get(camera_id)
            if entry is not None and (now - entry[1]) < self._stale_after_seconds:
                return entry[0]
            cached = self._standby_cache.get(camera_id)
            if cached is not None:
                return cached

        bgr = _render_empty_standby_frame(camera_id)
        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        jpg = buf.tobytes() if ok else b""
        with self._lock:
            self._standby_cache[camera_id] = jpg
        return jpg

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
