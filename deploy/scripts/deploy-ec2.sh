#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${WORKZONE_ENV_FILE:-/opt/workzone-vision/.env}"
COMPOSE_FILE="${WORKZONE_COMPOSE_FILE:-deploy/docker-compose.prod.yml}"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: Missing $ENV_FILE. Run deploy/scripts/ec2-bootstrap.sh first."
    exit 1
fi

echo "=== Deploying WorkZone Vision (PostgreSQL + Backend + Dashboard) ==="
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build --remove-orphans

echo "Waiting for health check at http://127.0.0.1/api/health ..."
for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1/api/health >/dev/null 2>&1; then
        echo "Health check PASSED:"
        curl -fsS http://127.0.0.1/api/health
        echo ""
        docker image prune -f >/dev/null 2>&1 || true
        exit 0
    fi
    sleep 2
done

echo "ERROR: Backend health check failed after 60s."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" logs --tail=80 backend frontend postgres
exit 1
