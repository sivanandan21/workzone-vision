"""Production FastAPI server backed by PostgreSQL on EC2 (reusing shared AWS Lambda handlers)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
LAMBDAS_DIR = ROOT / "aws" / "lambdas"
for p in (str(ROOT), str(LAMBDAS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from away_watchdog.handler import check_overdue_outside_persons
from backend.postgres_repository import PostgresStateRepository
from dashboard_api.handler import handle_dashboard_request
from ingest_processor.handler import process_crossing_payload
from shared.repository import StateRepository

SNAPSHOTS_DIR = Path(os.environ.get("SNAPSHOTS_DIR", "/app/edge/data/snapshots"))
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
AWAY_THRESHOLD_SECONDS = float(os.environ.get("AWAY_ALERT_THRESHOLD_SECONDS", "900"))
EDGE_INGEST_API_KEY = os.environ.get("EDGE_INGEST_API_KEY", "")


def create_repository() -> StateRepository:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url.startswith("postgres"):
        return PostgresStateRepository(database_url=database_url, init_schema=True)
    from tests.mock_aws_store import MockLocalStateStore
    return MockLocalStateStore(
        db_path=os.environ.get("LOCAL_STATE_DB", "edge/data/local_state.db"),
        snapshots_dir=str(SNAPSHOTS_DIR),
        away_alert_threshold_seconds=AWAY_THRESHOLD_SECONDS,
    )


_repo: Optional[StateRepository] = None


def get_repo() -> StateRepository:
    global _repo
    if _repo is None:
        _repo = create_repository()
    return _repo


async def _watchdog_loop() -> None:
    while True:
        try:
            repo = get_repo()
            check_overdue_outside_persons(
                repo=repo,
                away_alert_threshold_seconds=AWAY_THRESHOLD_SECONDS,
                now_utc=datetime.now(timezone.utc),
            )
        except Exception:
            pass
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_repo()
    task = asyncio.create_task(_watchdog_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="WorkZone Vision EC2 API", version="1.0.0", lifespan=lifespan)

cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "*").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/snapshots", StaticFiles(directory=str(SNAPSHOTS_DIR)), name="snapshots")


@app.get("/api/health")
def health_check() -> Dict[str, Any]:
    repo = get_repo()
    db_ok = True
    if isinstance(repo, PostgresStateRepository):
        db_ok = repo.ping()
    vexdet_onnx = Path(os.environ.get("VEXDET_ONNX_PATH", "/app/models/vexdet.onnx"))
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "postgresql" if isinstance(repo, PostgresStateRepository) else "sqlite_local",
        "database_connected": db_ok,
        "vexdet_onnx_present": vexdet_onnx.is_file(),
        "persons_tracked": len(repo.list_person_states()),
        "server_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _check_edge_key(request: Request) -> None:
    if EDGE_INGEST_API_KEY:
        provided = request.headers.get("x-edge-api-key", "")
        if provided != EDGE_INGEST_API_KEY:
            raise HTTPException(status_code=401, detail="Invalid X-Edge-Api-Key")


@app.post("/ingest/presign")
async def ingest_presign(request: Request) -> Dict[str, str]:
    _check_edge_key(request)
    body = await request.json()
    raw_key = str(body.get("s3_key", "")).lstrip("/")
    fname = Path(raw_key).name or "snapshot.jpg"
    s3_key = f"snapshots/{fname}"
    base_url = str(request.base_url).rstrip("/")
    return {
        "s3_key": s3_key,
        "upload_url": f"{base_url}/ingest/upload/{fname}",
    }


@app.put("/ingest/upload/{filename}")
async def ingest_upload_snapshot(filename: str, request: Request) -> Dict[str, str]:
    safe_name = Path(filename).name
    dest = SNAPSHOTS_DIR / safe_name
    data = await request.body()
    dest.write_bytes(data)
    return {"status": "uploaded", "path": f"snapshots/{safe_name}"}


@app.post("/ingest/crossing")
async def ingest_crossing(request: Request) -> Dict[str, Any]:
    _check_edge_key(request)
    payload = await request.json()
    return process_crossing_payload(
        payload_dict=payload,
        repo=get_repo(),
        away_alert_threshold_seconds=AWAY_THRESHOLD_SECONDS,
    )


@app.post("/api/simulator/run-demo")
def run_demo_scenario() -> Dict[str, Any]:
    """Run synthetic 2-camera nested-zone scenario directly into PostgreSQL for quick live demo."""
    from tests.simulator_cli import run_simulation

    repo = get_repo()
    tmp_dir = Path(tempfile.mkdtemp(prefix="wz_demo_"))
    try:
        sim = run_simulation(
            synthetic=True,
            work_dir=tmp_dir,
            strict_target_mode=True,
            away_alert_threshold_seconds=AWAY_THRESHOLD_SECONDS,
        )
        mock_s3 = tmp_dir / "mock_s3"
        if mock_s3.is_dir():
            for jpg in mock_s3.glob("*.jpg"):
                shutil.copy2(jpg, SNAPSHOTS_DIR / jpg.name)

        ingested = 0
        # Replay chronological logs as edge crossing events into active DB
        for log in reversed(sim["logs"]):
            dir_map = {
                ("OUTSIDE", "IN_ROOM_1"): "CAM1_INWARD",
                ("IN_ROOM_1", "IN_ROOM_2"): "CAM2_INWARD",
                ("IN_ROOM_2", "IN_ROOM_1"): "CAM2_OUTWARD",
                ("IN_ROOM_1", "OUTSIDE"): "CAM1_OUTWARD",
            }
            direction = dir_map.get((log["from_zone"], log["to_zone"]), "CAM1_INWARD")
            res = process_crossing_payload(
                payload_dict={
                    "event_id": log["event_id"],
                    "person_id": log["person_id"],
                    "display_name": log["display_name"],
                    "camera_id": log["camera_id"],
                    "direction": direction,
                    "timestamp": log["timestamp"],
                    "confidence": log["confidence"],
                    "snapshot_s3_key": log.get("snapshot_s3_key"),
                    "reid_method": "demo_simulator",
                },
                repo=repo,
                away_alert_threshold_seconds=AWAY_THRESHOLD_SECONDS,
            )
            if res.get("status") == "processed":
                ingested += 1
        return {"status": "ok", "events_ingested": ingested}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _delegate_dashboard(request: Request) -> Response:
    raw_body = await request.body()
    body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    actor = request.headers.get("x-local-actor", "authority@workzone.local")
    groups_raw = request.headers.get("x-local-groups", "Authorities")
    groups = [g.strip() for g in groups_raw.split(",") if g.strip()]

    resp = handle_dashboard_request(
        method=request.method.upper(),
        path=request.url.path,
        query_params=dict(request.query_params),
        body=body,
        repo=get_repo(),
        actor=actor,
        groups=groups,
        snapshot_url_resolver=lambda k: f"/{k.lstrip('/')}",
    )
    return Response(
        content=resp["body"],
        status_code=resp["statusCode"],
        media_type=resp.get("headers", {}).get("Content-Type", "application/json"),
        headers={
            k: v
            for k, v in resp.get("headers", {}).items()
            if k.lower() != "content-type"
        },
    )


@app.api_route("/api/state", methods=["GET"])
@app.api_route("/api/logs", methods=["GET"])
@app.api_route("/api/logs/export", methods=["GET"])
@app.api_route("/api/logs/{log_id}/verify", methods=["POST"])
@app.api_route("/api/logs/{log_id}/flag", methods=["POST"])
@app.api_route("/api/logs/{log_id}/adjust", methods=["PATCH"])
async def dashboard_routes(request: Request) -> Response:
    return await _delegate_dashboard(request)
