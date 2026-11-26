# WorkZone Vision — 2-Camera Nested-Zone Tracking & Authority Verification

Production multi-camera movement tracking, away-duration auditing, and Authority Verification platform powered by **VEXDET** ([`sivanandan21/VEXDET`](https://github.com/sivanandan21/VEXDET)) Apache-2.0 RF-DETR ONNX detection.

Supports **both** deployment targets sharing the exact same nested-zone state machine:
1. **AWS EC2 + PostgreSQL + Docker Compose + Jenkins + GitHub Webhook** (`backend/`, `deploy/`, `Jenkinsfile`).
2. **AWS Serverless SAM** (`aws/`: Lambda, DynamoDB, S3, SNS, Cognito).

## Physical Topology & Away Timer Logic

- **Room 1 (Outer Room):** Only external doorway (**Camera 1** facing Main Entry/Exit).
- **Room 2 (Inner Room):** No outside doors (**Camera 2** mounted inside Room 2 above the Connecting Door).
- **Away Timer Rules:**
  - `OUTSIDE -> IN_ROOM_1` (Cam 1 Inward): Stops away timer and computes `away_duration = T_return - T_exit`.
  - `IN_ROOM_1 <-> IN_ROOM_2` (Cam 2 Connecting Door): Logged as internal shifts (**never starts away timer**).
  - `IN_ROOM_1 -> OUTSIDE` (Cam 1 Outward): Starts away timer (`T_exit`).
  - Fail-safe reconciliation handles missed Cam 2 or Cam 1 crossings automatically and flags them `PENDING_REVIEW`.

## Repository Layout

- `configs/`: Camera tripwire coordinates, VEXDET ONNX thresholds, 2-student enrollment & ReID config.
- `edge/`: Multi-threaded RTSP latest-frame capture, VEXDET blur/CLAHE utilities, `VexdetOnnxDetector`, independent `ByteTrack` trackers, bidirectional `LineZone` tripwires with 4s debounce, crossing-only ReID, and SQLite WAL offline queue + HTTPS/MQTT publisher.
- `backend/`: `PostgresStateRepository` + FastAPI production server for AWS EC2 Docker deployment.
- `aws/`: AWS SAM IaC (`template.yaml`), shared state machine & authority verification engine, Lambda handlers.
- `dashboard/`: React + TypeScript + Tailwind Authority Verification Dashboard (`RoomStatusBoard`, `MovementLogsTable`, `AuthorityActionModal`, `SnapshotPreviewModal`, Cognito + local RBAC).
- `deploy/`: Production `docker-compose.prod.yml`, Nginx reverse proxy, Ubuntu 24.04 EC2 bootstrap & Jenkins deploy scripts.
- `tests/`: Local dual-MP4 simulator CLI, `MockLocalStateStore`, local mock API server, and full pytest suite.

## Quick Local Verification

```bash
# 1. Run Python unit, PostgreSQL repo, and dual-MP4 simulator E2E tests
pytest tests/ -v

# 2. Run Dashboard typecheck, Vitest & production build
cd dashboard && npm ci && npx tsc --noEmit && npm test -- --run && npm run build
```

See [docs/VEXDET_ONNX_CONTRACT.md](docs/VEXDET_ONNX_CONTRACT.md) and [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) for EC2 MobaXterm setup, Jenkins + GitHub Webhook automation, and VEXDET ONNX export steps.
