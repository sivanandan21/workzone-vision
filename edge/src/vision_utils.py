"""Lightweight edge image quality, CLAHE preprocessing, and IoU deduplication utilities.

Derived directly from VEXDET (sivanandan21/VEXDET) without heavy PyTorch dependencies.
"""

from __future__ import annotations

from typing import List, Optional, Tuple
import cv2
import numpy as np


def compute_blur_score(frame_or_gray: np.ndarray) -> float:
    """Calculate image sharpness using variance of the Laplacian (from VEXDET)."""
    if frame_or_gray is None or frame_or_gray.size == 0:
        return 0.0
    if frame_or_gray.ndim == 3:
        gray = cv2.cvtColor(frame_or_gray, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame_or_gray
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    return float(laplacian.var())


def is_frame_sharp(frame_or_gray: np.ndarray, blur_threshold: float = 100.0) -> Tuple[bool, float]:
    """Return (is_sharp, score) comparing Laplacian variance against blur_threshold."""
    score = compute_blur_score(frame_or_gray)
    return score >= float(blur_threshold), score


def enhance_cctv_frame(
    image_rgb: np.ndarray,
    clip_limit: float = 2.0,
    tile_grid: Tuple[int, int] = (8, 8),
) -> np.ndarray:
    """Apply LAB L-channel CLAHE enhancement on an RGB image (from VEXDET)."""
    if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
        raise ValueError(f"Expected RGB image (H, W, 3), got {image_rgb.shape}")
    if image_rgb.dtype != np.uint8:
        image_rgb = np.clip(image_rgb, 0, 255).astype(np.uint8)

    lab = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=float(clip_limit),
        tileGridSize=(int(tile_grid[0]), int(tile_grid[1])),
    )
    l_enhanced = clahe.apply(l_ch)
    merged = cv2.merge([l_enhanced, a_ch, b_ch])
    return cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)


def compute_iou_matrix(boxes_a: np.ndarray, boxes_b: np.ndarray) -> np.ndarray:
    """Pairwise IoU matrix between [x1, y1, x2, y2] arrays."""
    if len(boxes_a) == 0 or len(boxes_b) == 0:
        return np.zeros((len(boxes_a), len(boxes_b)), dtype=np.float32)

    area_a = np.maximum(0.0, boxes_a[:, 2] - boxes_a[:, 0]) * np.maximum(0.0, boxes_a[:, 3] - boxes_a[:, 1])
    area_b = np.maximum(0.0, boxes_b[:, 2] - boxes_b[:, 0]) * np.maximum(0.0, boxes_b[:, 3] - boxes_b[:, 1])

    x1 = np.maximum(boxes_a[:, None, 0], boxes_b[None, :, 0])
    y1 = np.maximum(boxes_a[:, None, 1], boxes_b[None, :, 1])
    x2 = np.minimum(boxes_a[:, None, 2], boxes_b[None, :, 2])
    y2 = np.minimum(boxes_a[:, None, 3], boxes_b[None, :, 3])

    inter = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0.0, inter / union, 0.0).astype(np.float32)


def deduplicate_detections(
    boxes: np.ndarray,
    scores: np.ndarray,
    class_ids: np.ndarray,
    class_names: List[str],
    iou_threshold: float = 0.50,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
    """Class-agnostic greedy NMS suppressing duplicate overlapping boxes on the same person."""
    if len(boxes) <= 1:
        return boxes, scores, class_ids, class_names

    order = np.argsort(-scores)
    keep_indices: List[int] = []
    ious = compute_iou_matrix(boxes, boxes)

    suppressed = np.zeros(len(boxes), dtype=bool)
    for idx in order:
        if suppressed[idx]:
            continue
        keep_indices.append(int(idx))
        overlap = ious[idx] >= iou_threshold
        suppressed |= overlap

    kept = np.array(keep_indices, dtype=np.int32)
    return (
        boxes[kept],
        scores[kept],
        class_ids[kept],
        [class_names[i] for i in keep_indices],
    )


def annotate_monitor_frame(
    frame_bgr: np.ndarray,
    boxes_with_labels: List[Tuple[Tuple[float, float, float, float], str]],
    tripwire_segment: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None,
    tripwire_label: str = "tripwire",
) -> np.ndarray:
    """Draw green bounding boxes with green badge labels and optional blue doorway tripwire."""
    canvas = frame_bgr.copy()

    if tripwire_segment is not None:
        (x1, y1), (x2, y2) = tripwire_segment
        overlay = canvas.copy()
        cv2.line(overlay, (x1, y1), (x2, y2), (240, 175, 60), 14, cv2.LINE_AA)
        cv2.addWeighted(overlay, 0.28, canvas, 0.72, 0, canvas)
        cv2.line(canvas, (x1, y1), (x2, y2), (245, 195, 90), 2, cv2.LINE_AA)
        cv2.line(canvas, (x1, y1 - 10), (x1, y1 + 10), (245, 195, 90), 2, cv2.LINE_AA)
        cv2.line(canvas, (x2, y2 - 10), (x2, y2 + 10), (245, 195, 90), 2, cv2.LINE_AA)

        if tripwire_label:
            mx = (x1 + x2) // 2
            my = (y1 + y2) // 2
            (tw, th), _ = cv2.getTextSize(tripwire_label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
            cv2.putText(
                canvas,
                tripwire_label,
                (mx - tw // 2, my + th // 2 - 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

    for (bx1, by1, bx2, by2), label in boxes_with_labels:
        ix1, iy1, ix2, iy2 = int(bx1), int(by1), int(bx2), int(by2)
        box_color = (95, 235, 95)
        cv2.rectangle(canvas, (ix1, iy1), (ix2, iy2), box_color, 2, cv2.LINE_AA)

        (lw, lh), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 2)
        tag_y1 = max(0, iy1 - lh - baseline - 6)
        tag_x2 = min(canvas.shape[1] - 1, ix1 + lw + 10)
        cv2.rectangle(canvas, (ix1, tag_y1), (tag_x2, iy1), box_color, -1)
        cv2.putText(
            canvas,
            label,
            (ix1 + 5, iy1 - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (15, 35, 15),
            2,
            cv2.LINE_AA,
        )

    return canvas
