# Real CCTV Camera Setup Guide

Both camera viewports on `http://43.204.146.167/` start cleanly in **`STANDBY`** (`No Signal`) until real RTSP/camera frames are pushed by the `edge` pipeline.

---

## Step 1: Prepare VEXDET ONNX Weights & Student Photos

1. **Export VEXDET ONNX model** (from `sivanandan21/VEXDET`):
   ```bash
   vexdet export --checkpoint models/checkpoints/best.pt --model-name RFDETRNano
   ```
   Copy the exported `.onnx` file to `models/vexdet.onnx`.
2. **Add Student 1 & Student 2 Reference Photos** (for strict 2-student verification):
   - Place 5–15 clear photos of Student 1 in `data/enrolled_students/student_1/`
   - Place 5–15 clear photos of Student 2 in `data/enrolled_students/student_2/`
3. **Verify Tripwire Lines (`configs/system_config.yaml`)**:
   - `cameras.cam_1.tripwire`: Main Entry/Exit line inside Room 1 (`p1`, `p2`, `inward_normal`)
   - `cameras.cam_2.tripwire`: Connecting Door line mounted inside Room 2 (`p1`, `p2`, `inward_normal`)

---

## Step 2: Choose Where Your Cameras Are Reachable

### Option A — Cameras Are on Your Local Office/College Wi-Fi / LAN (Recommended)
Because local `192.168.x.x` CCTV cameras cannot be reached directly from AWS EC2 without router port-forwarding/VPN, run `edge/` on a Windows/Linux PC on the **same LAN** as the cameras, pushing detections + live MJPEG frames to EC2 (`http://43.204.146.167`):

1. Get your `EDGE_INGEST_API_KEY` from EC2 MobaXterm:
   ```bash
   grep EDGE_INGEST_API_KEY /opt/workzone-vision/.env
   ```
2. Install edge dependencies on your local PC (Python 3.10+ recommended, or Python 3.8+ with `edge/requirements.txt`):
   ```bash
   pip install -r edge/requirements.txt
   ```
3. **Test camera connectivity & push 1 test frame to EC2**:
   ```bash
   python -m edge.src.edge_runner \
     --cam1 "rtsp://admin:PASSWORD@192.168.1.101:554/Streaming/Channels/101" \
     --cam2 "rtsp://admin:PASSWORD@192.168.1.102:554/Streaming/Channels/101" \
     --api-url "http://43.204.146.167" \
     --api-key "YOUR_EDGE_INGEST_API_KEY" \
     --onnx "models/vexdet.onnx" \
     --test-cameras
   ```
4. **Start live 2-camera tracking + streaming**:
   ```bash
   python -m edge.src.edge_runner \
     --cam1 "rtsp://admin:PASSWORD@192.168.1.101:554/Streaming/Channels/101" \
     --cam2 "rtsp://admin:PASSWORD@192.168.1.102:554/Streaming/Channels/101" \
     --api-url "http://43.204.146.167" \
     --api-key "YOUR_EDGE_INGEST_API_KEY" \
     --onnx "models/vexdet.onnx"
   ```
   *(You can also pass `--cam1 0 --cam2 1` to test with local USB webcams, or local `.mp4` paths.)*

---

### Option B — Cameras Have Public RTSP / VPN Reachable Directly From EC2 (`43.204.146.167`)
1. Copy `models/vexdet.onnx` to `/opt/workzone-vision/models/vexdet.onnx` and student photos to `/opt/workzone-vision/enrolled_students/student_1/` and `student_2/`.
2. Edit `/opt/workzone-vision/.env` on EC2 to set `CAM_1_SOURCE` and `CAM_2_SOURCE`.
3. Start the `edge` container on EC2:
   ```bash
   cd ~/workzone-vision
   docker compose -p workzone-vision --env-file /opt/workzone-vision/.env -f deploy/docker-compose.prod.yml --profile live-cameras up -d --build edge
   ```

As soon as frames arrive, both camera badges on `http://43.204.146.167/` switch from **`STANDBY`** to **`LIVE`** with real CCTV video, green student bounding boxes, and the Room 1 `tripwire` overlay.
