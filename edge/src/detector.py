"""VEXDET RF-DETR ONNX inference adapter for edge person detection.

Enforces Apache-2.0 RF-DETR compatibility, BGR->RGB conversion, optional CLAHE
preprocessing, Laplacian blur evaluation, and class-agnostic deduplication.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple
import cv2
import numpy as np

from edge.src.config import ALLOWED_VEXDET_MODELS, VexdetEdgeConfig
from edge.src.vision_utils import (
    compute_blur_score,
    deduplicate_detections,
    enhance_cctv_frame,
)

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


@dataclass
class DetectionBatch:
    """Uniform edge detection container compatible with supervision.Detections."""

    boxes_xyxy: np.ndarray      # shape (N, 4) float32
    scores: np.ndarray          # shape (N,) float32
    class_ids: np.ndarray       # shape (N,) int32
    class_names: List[str]
    blur_score: float = 0.0

    def __len__(self) -> int:
        return len(self.boxes_xyxy)

    def to_supervision(self) -> Any:
        try:
            import supervision as sv
            return sv.Detections(
                xyxy=self.boxes_xyxy,
                confidence=self.scores,
                class_id=self.class_ids,
                data={"class_name": np.array(self.class_names, dtype=object)},
            )
        except ImportError:
            return self


class PersonDetectorBackend(Protocol):
    def detect(self, frame_bgr: np.ndarray) -> DetectionBatch:
        ...


class VexdetOnnxDetector:
    """Lightweight ONNX Runtime detector following the VEXDET RF-DETR ONNX contract."""

    def __init__(
        self,
        config: VexdetEdgeConfig,
        coco_person_class_id: int = 1,
        class_id_to_name: Optional[Dict[int, str]] = None,
    ) -> None:
        if config.model_name not in ALLOWED_VEXDET_MODELS:
            raise ValueError(f"Model '{config.model_name}' not in Apache-2.0 whitelist {ALLOWED_VEXDET_MODELS}")

        self.config = config
        self.coco_person_class_id = coco_person_class_id
        self.class_id_to_name = class_id_to_name or {0: "person", 1: "person"}
        self._rfdetr_predictor = None
        self._ort_session = None
        self._input_name = "input"
        self._input_hw: Tuple[int, int] = config.input_size

        onnx_file = Path(config.onnx_path)
        if not onnx_file.is_file():
            raise FileNotFoundError(
                f"VEXDET ONNX model not found at '{onnx_file}'. "
                "Export via `vexdet export` and place it at models/vexdet.onnx."
            )
        self._load_runtime(onnx_file)

    def _load_runtime(self, onnx_file: Path) -> None:
        # Prefer rfdetr.RFDETRInference if rfdetr[onnx] is installed; otherwise use onnxruntime directly.
        try:
            from rfdetr import RFDETRInference
            self._rfdetr_predictor = RFDETRInference(str(onnx_file))
            return
        except ImportError:
            pass

        import onnxruntime as ort
        available = ort.get_available_providers()
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if "CUDAExecutionProvider" in available else ["CPUExecutionProvider"]
        self._ort_session = ort.InferenceSession(str(onnx_file), providers=providers)
        input_meta = self._ort_session.get_inputs()[0]
        self._input_name = input_meta.name
        shape = input_meta.shape
        if len(shape) == 4 and isinstance(shape[2], int) and isinstance(shape[3], int):
            self._input_hw = (int(shape[2]), int(shape[3]))

    def preprocess_bgr(self, frame_bgr: np.ndarray) -> Tuple[np.ndarray, float]:
        """Convert BGR->RGB, evaluate Laplacian blur score, and apply optional CLAHE."""
        blur_score = compute_blur_score(frame_bgr)
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        if self.config.enable_clahe:
            frame_rgb = enhance_cctv_frame(
                frame_rgb,
                clip_limit=self.config.clahe_clip_limit,
                tile_grid=self.config.clahe_tile_grid,
            )
        return frame_rgb, blur_score

    def _prepare_tensor(self, frame_rgb: np.ndarray) -> np.ndarray:
        in_h, in_w = self._input_hw
        resized = cv2.resize(frame_rgb, (in_w, in_h), interpolation=cv2.INTER_LINEAR)
        arr = resized.astype(np.float32) / 255.0
        arr = (arr - IMAGENET_MEAN) / IMAGENET_STD
        nchw = np.transpose(arr, (2, 0, 1))[None, ...].astype(np.float32)
        return np.ascontiguousarray(nchw)

    def _decode_onnx_outputs(
        self,
        outputs: List[np.ndarray],
        orig_h: int,
        orig_w: int,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
        if len(outputs) < 2:
            return (
                np.empty((0, 4), dtype=np.float32),
                np.empty((0,), dtype=np.float32),
                np.empty((0,), dtype=np.int32),
                [],
            )

        raw_boxes = np.asarray(outputs[0])
        raw_logits = np.asarray(outputs[1])

        if raw_boxes.ndim == 3:
            raw_boxes = raw_boxes[0]
        if raw_logits.ndim == 3:
            raw_logits = raw_logits[0]

        # Apply sigmoid if logits are outside [0, 1]
        if raw_logits.size > 0 and (raw_logits.min() < 0.0 or raw_logits.max() > 1.0):
            probs = 1.0 / (1.0 + np.exp(-np.clip(raw_logits, -30.0, 30.0)))
        else:
            probs = raw_logits

        if probs.size == 0 or raw_boxes.size == 0:
            return (
                np.empty((0, 4), dtype=np.float32),
                np.empty((0,), dtype=np.float32),
                np.empty((0,), dtype=np.int32),
                [],
            )

        class_ids = np.argmax(probs, axis=-1).astype(np.int32)
        scores = probs[np.arange(len(probs)), class_ids].astype(np.float32)

        allowed_names = {c.lower() for c in self.config.target_classes} | {
            k.lower() for k in self.config.custom_class_map.keys()
        }

        kept_boxes: List[List[float]] = []
        kept_scores: List[float] = []
        kept_cids: List[int] = []
        kept_names: List[str] = []

        is_normalized = bool(np.max(np.abs(raw_boxes)) <= 1.5)
        for box, score, cid in zip(raw_boxes, scores, class_ids):
            if float(score) < self.config.confidence_threshold:
                continue
            cname = self.class_id_to_name.get(int(cid), "person" if int(cid) in (0, self.coco_person_class_id) else f"class_{cid}")
            if allowed_names and cname.lower() not in allowed_names:
                continue

            if is_normalized:
                cx, cy, bw, bh = [float(v) for v in box]
                x1 = np.clip((cx - bw / 2.0) * orig_w, 0.0, float(orig_w))
                y1 = np.clip((cy - bh / 2.0) * orig_h, 0.0, float(orig_h))
                x2 = np.clip((cx + bw / 2.0) * orig_w, 0.0, float(orig_w))
                y2 = np.clip((cy + bh / 2.0) * orig_h, 0.0, float(orig_h))
            else:
                x1, y1, x2, y2 = [float(v) for v in box]
                x1 = float(np.clip(x1, 0.0, float(orig_w)))
                y1 = float(np.clip(y1, 0.0, float(orig_h)))
                x2 = float(np.clip(x2, 0.0, float(orig_w)))
                y2 = float(np.clip(y2, 0.0, float(orig_h)))

            if x2 - x1 < 4.0 or y2 - y1 < 8.0:
                continue

            kept_boxes.append([x1, y1, x2, y2])
            kept_scores.append(float(score))
            kept_cids.append(int(cid))
            kept_names.append(cname)

        if not kept_boxes:
            return (
                np.empty((0, 4), dtype=np.float32),
                np.empty((0,), dtype=np.float32),
                np.empty((0,), dtype=np.int32),
                [],
            )

        return (
            np.asarray(kept_boxes, dtype=np.float32),
            np.asarray(kept_scores, dtype=np.float32),
            np.asarray(kept_cids, dtype=np.int32),
            kept_names,
        )

    def detect(self, frame_bgr: np.ndarray) -> DetectionBatch:
        """Run VEXDET ONNX person detection on a BGR frame."""
        orig_h, orig_w = frame_bgr.shape[:2]
        frame_rgb, blur_score = self.preprocess_bgr(frame_bgr)

        if self._rfdetr_predictor is not None:
            raw_dets = self._rfdetr_predictor.predict(frame_rgb, threshold=self.config.confidence_threshold)
            boxes = np.asarray(raw_dets.xyxy, dtype=np.float32)
            scores = np.asarray(raw_dets.confidence, dtype=np.float32)
            cids = np.asarray(raw_dets.class_id, dtype=np.int32)
            raw_names = (raw_dets.data or {}).get("class_name", ["person"] * len(boxes))
            names = [str(n) for n in raw_names]
            # Filter to target classes
            allowed = {c.lower() for c in self.config.target_classes} | {
                k.lower() for k in self.config.custom_class_map.keys()
            }
            mask = [i for i, n in enumerate(names) if n.lower() in allowed]
            if mask:
                boxes = boxes[mask]
                scores = scores[mask]
                cids = cids[mask]
                names = [names[i] for i in mask]
            else:
                boxes = np.empty((0, 4), dtype=np.float32)
                scores = np.empty((0,), dtype=np.float32)
                cids = np.empty((0,), dtype=np.int32)
                names = []
        else:
            tensor = self._prepare_tensor(frame_rgb)
            outputs = self._ort_session.run(None, {self._input_name: tensor})
            boxes, scores, cids, names = self._decode_onnx_outputs(outputs, orig_h, orig_w)

        boxes, scores, cids, names = deduplicate_detections(
            boxes=boxes,
            scores=scores,
            class_ids=cids,
            class_names=names,
            iou_threshold=self.config.dedup_iou_threshold,
        )

        return DetectionBatch(
            boxes_xyxy=boxes,
            scores=scores,
            class_ids=cids,
            class_names=names,
            blur_score=blur_score,
        )
