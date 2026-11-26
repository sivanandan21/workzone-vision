# VEXDET ONNX Inference Contract

`workzone-vision` separates heavy PyTorch fine-tuning (which lives in [`sivanandan21/VEXDET`](https://github.com/sivanandan21/VEXDET)) from lightweight edge deployment (`edge/`).

## 1. Exporting Weights from VEXDET

In your VEXDET training environment:

```bash
vexdet export --checkpoint models/checkpoints/best.pt --model-name RFDETRNano
```

Copy the exported artifact (e.g., `models/exported/vexdet_rfdetrnano.onnx`) to `models/vexdet.onnx` in `workzone-vision` (or set `VEXDET_ONNX_PATH`).

Permitted Apache-2.0 RF-DETR architectures:
- `RFDETRNano` (recommended for CPU edge nodes)
- `RFDETRSmall`
- `RFDETRMedium`
- `RFDETRLarge`

## 2. Input & Preprocessing Contract

1. **Color Space Integrity**: OpenCV captures BGR frames; `edge/src/detector.py` converts frames via `cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)` before preprocessing and inference.
2. **Quality & Low-Light Preprocessing** (derived from VEXDET `src/extract_frames.py` & `src/preprocess.py`):
   - **Laplacian Blur Filter**: `compute_blur_score(gray)` (`cv2.Laplacian(gray, cv2.CV_64F).var()`) scores candidate crops and prevents motion-blurred crops from polluting ReID or S3 snapshots.
   - **CLAHE**: `enhance_cctv_frame(rgb, clip_limit=2.0, tile_grid=(8, 8))` enhances the L-channel in LAB space for shadowed doorways.
3. **Tensor Format**:
   - Shape: `[1, 3, H, W]` (`float32`), RGB NCHW layout (`H, W` read dynamically from the ONNX input tensor shape or `configs/system_config.yaml` default `[560, 560]`).
   - Normalization: pixel values scaled to `[0.0, 1.0]` then ImageNet normalized with `mean=[0.485, 0.456, 0.406]`, `std=[0.229, 0.224, 0.225]`.

## 3. Output & Post-Processing Contract

`edge/src/detector.py` supports:
1. **`rfdetr.RFDETRInference` adapter** (when `rfdetr` is installed).
2. **Direct `onnxruntime.InferenceSession` decoding** (lightweight edge mode without `rfdetr`/PyTorch):
   - Expects RF-DETR ONNX outputs `(dets, labels)` where `dets` has shape `[1, Q, 4]` normalized `[cx, cy, w, h]` (or `[x1, y1, x2, y2]` when detected in pixel coordinates) and `labels` has shape `[1, Q, C]` raw logits (`sigmoid` applied).
   - Maps class indices to target names (`person` for COCO pretrained weights, or custom classes such as `student_1`, `student_2` when fine-tuned).
   - Filters `confidence >= confidence_threshold` and runs class-agnostic IoU deduplication (`dedup_iou_threshold`) to eliminate duplicate detections on a single individual.
