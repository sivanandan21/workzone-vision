"""Unit tests for VEXDET ONNX adapter, Laplacian blur filtering, CLAHE, and IoU deduplication."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from edge.src.config import VexdetEdgeConfig
from edge.src.detector import VexdetOnnxDetector
from edge.src.vision_utils import (
    compute_blur_score,
    deduplicate_detections,
    enhance_cctv_frame,
)


def test_vexdet_apache_model_whitelist() -> None:
    assert VexdetEdgeConfig(model_name="RFDETRNano").model_name == "RFDETRNano"
    for forbidden in ("YOLOv8", "YOLO11", "ultralytics"):
        with pytest.raises(ValueError):
            VexdetEdgeConfig(model_name=forbidden)


def test_laplacian_blur_and_clahe() -> None:
    sharp = np.zeros((160, 160, 3), dtype=np.uint8)
    cv2.rectangle(sharp, (30, 30), (130, 130), (220, 220, 220), 2)
    cv2.putText(sharp, "VEXDET", (35, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    blurry = cv2.GaussianBlur(sharp, (15, 15), 5.0)

    assert compute_blur_score(sharp) > compute_blur_score(blurry) * 3.0

    dark_rgb = np.full((120, 120, 3), 25, dtype=np.uint8)
    dark_rgb[30:90, 30:90] = 60
    enhanced = enhance_cctv_frame(dark_rgb, clip_limit=2.0, tile_grid=(8, 8))
    assert enhanced.shape == dark_rgb.shape
    assert enhanced.dtype == np.uint8
    assert np.std(enhanced) > np.std(dark_rgb)


def test_deduplicate_overlapping_boxes() -> None:
    boxes = np.array(
        [
            [100, 100, 200, 300],
            [104, 102, 198, 298],  # Duplicate detection on same person
            [350, 110, 440, 310],
        ],
        dtype=np.float32,
    )
    scores = np.array([0.92, 0.78, 0.88], dtype=np.float32)
    cids = np.array([0, 0, 0], dtype=np.int32)
    names = ["person", "person", "person"]

    d_boxes, d_scores, _, _ = deduplicate_detections(boxes, scores, cids, names, iou_threshold=0.50)
    assert len(d_boxes) == 2
    assert float(d_scores[0]) == pytest.approx(0.92)


def test_decode_rfdetr_onnx_outputs() -> None:
    # Construct adapter bypassing file check to test ONNX tensor decoding directly
    detector = VexdetOnnxDetector.__new__(VexdetOnnxDetector)
    detector.config = VexdetEdgeConfig(
        target_classes=["person"],
        custom_class_map={"student_1": "001", "student_2": "002"},
        confidence_threshold=0.45,
        dedup_iou_threshold=0.50,
    )
    detector.coco_person_class_id = 1
    detector.class_id_to_name = {0: "person", 1: "student_1", 2: "chair"}

    # Normalized cxcywh boxes + raw logits (sigmoid(3.0) ~= 0.95, sigmoid(-3.0) ~= 0.047)
    raw_boxes = np.array([[[0.5, 0.5, 0.2, 0.4], [0.2, 0.2, 0.1, 0.1], [0.8, 0.5, 0.15, 0.35]]], dtype=np.float32)
    raw_logits = np.array(
        [
            [
                [3.0, -4.0, -4.0],   # person ~0.95 (KEEP)
                [-4.0, -4.0, 3.0],   # chair ~0.95 (FILTER OUT)
                [-4.0, 2.5, -4.0],   # student_1 ~0.92 (KEEP)
            ]
        ],
        dtype=np.float32,
    )
    boxes, scores, cids, names = detector._decode_onnx_outputs([raw_boxes, raw_logits], orig_h=480, orig_w=640)
    assert len(boxes) == 2
    assert names == ["person", "student_1"]
    assert boxes[0][0] == pytest.approx(256.0)
    assert boxes[0][2] == pytest.approx(384.0)
