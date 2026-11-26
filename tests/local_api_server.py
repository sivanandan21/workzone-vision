"""Local HTTP server exposing AWS Lambda dashboard & ingest handlers backed by MockLocalStateStore."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
LAMBDAS_DIR = ROOT / "aws" / "lambdas"
for p in (str(ROOT), str(LAMBDAS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from dashboard_api.handler import handle_dashboard_request
from tests.mock_aws_store import MockLocalStateStore


def create_local_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    store: MockLocalStateStore = None,
) -> ThreadingHTTPServer:
    state_store = store or MockLocalStateStore()

    class RequestHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization,Content-Type,X-Edge-Api-Key,X-Local-Actor,X-Local-Groups")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self._send(204, "text/plain", b"")

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path

            if method == "GET" and path.startswith("/snapshots/"):
                fname = Path(path).name
                img_path = state_store.snapshots_dir / fname
                if img_path.is_file():
                    self._send(200, "image/jpeg", img_path.read_bytes())
                else:
                    self._send(404, "application/json", b'{"error":"Snapshot not found"}')
                return

            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}

            if method == "POST" and path == "/ingest/crossing":
                res = state_store.ingest_edge_event(body)
                self._send(200, "application/json", json.dumps(res).encode("utf-8"))
                return

            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            actor = self.headers.get("X-Local-Actor", "local-authority@workzone.local")
            groups_raw = self.headers.get("X-Local-Groups", "Authorities")
            groups = [g.strip() for g in groups_raw.split(",") if g.strip()]

            resp = handle_dashboard_request(
                method=method,
                path=path,
                query_params=query,
                body=body,
                repo=state_store,
                actor=actor,
                groups=groups,
                snapshot_url_resolver=lambda k: f"/{k.lstrip('/')}",
            )
            ctype = resp.get("headers", {}).get("Content-Type", "application/json")
            self._send(resp["statusCode"], ctype, resp["body"].encode("utf-8"))

        def log_message(self, format: str, *args) -> None:
            return

    return ThreadingHTTPServer((host, port), RequestHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local WorkZone Vision mock AWS API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db-path", default="edge/data/local_state.db")
    parser.add_argument("--snapshots-dir", default="edge/data/mock_s3")
    args = parser.parse_args()

    store = MockLocalStateStore(db_path=args.db_path, snapshots_dir=args.snapshots_dir)
    server = create_local_server(args.host, args.port, store)
    print(f"WorkZone local mock API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
