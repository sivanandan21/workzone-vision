"""Multi-threaded RTSP / MP4 capture with latest-frame buffer (zero frame backlog)."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Optional, Union
import cv2
import numpy as np


@dataclass
class FramePacket:
    camera_id: str
    frame_bgr: np.ndarray
    frame_seq: int
    timestamp_epoch: float


class LatestFrameStream:
    """Continuously grabs frames on a daemon thread and retains ONLY the latest frame."""

    def __init__(
        self,
        camera_id: str,
        source: Union[str, int],
        reconnect_delay_sec: float = 1.5,
    ) -> None:
        self.camera_id = camera_id
        self.source = int(source) if isinstance(source, str) and source.isdigit() else source
        self.reconnect_delay_sec = reconnect_delay_sec

        self._lock = threading.Lock()
        self._latest_packet: Optional[FramePacket] = None
        self._frame_seq = 0
        self._dropped_frames = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "LatestFrameStream":
        if self._running:
            return self
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"capture-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def read_latest(self, last_seen_seq: int = -1) -> Optional[FramePacket]:
        """Return newest frame if its sequence exceeds last_seen_seq; otherwise None."""
        with self._lock:
            if self._latest_packet is None:
                return None
            if self._latest_packet.frame_seq <= last_seen_seq:
                return None
            return self._latest_packet

    @property
    def dropped_frames(self) -> int:
        with self._lock:
            return self._dropped_frames

    def _capture_loop(self) -> None:
        cap: Optional[cv2.VideoCapture] = None
        while self._running:
            if cap is None or not cap.isOpened():
                cap = cv2.VideoCapture(self.source)
                if not cap.isOpened():
                    time.sleep(self.reconnect_delay_sec)
                    continue
                # Request minimal internal OpenCV buffer when backend honors it
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            ret, frame = cap.read()
            if not ret or frame is None:
                cap.release()
                cap = None
                time.sleep(self.reconnect_delay_sec)
                continue

            now = time.time()
            with self._lock:
                self._frame_seq += 1
                if self._latest_packet is not None:
                    self._dropped_frames += 1
                self._latest_packet = FramePacket(
                    camera_id=self.camera_id,
                    frame_bgr=frame,
                    frame_seq=self._frame_seq,
                    timestamp_epoch=now,
                )

        if cap is not None:
            cap.release()


class SequentialVideoReader:
    """Deterministic frame-by-frame MP4 reader for offline dual-video simulation & tests."""

    def __init__(self, camera_id: str, video_path: str) -> None:
        self.camera_id = camera_id
        self.video_path = video_path
        self._cap = cv2.VideoCapture(video_path)
        if not self._cap.isOpened():
            raise FileNotFoundError(f"Unable to open video source '{video_path}' for {camera_id}")
        self.fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 15.0)
        self.frame_seq = 0

    def read_next(self) -> Optional[FramePacket]:
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return None
        self.frame_seq += 1
        return FramePacket(
            camera_id=self.camera_id,
            frame_bgr=frame,
            frame_seq=self.frame_seq,
            timestamp_epoch=self.frame_seq / max(self.fps, 1.0),
        )

    def release(self) -> None:
        self._cap.release()
