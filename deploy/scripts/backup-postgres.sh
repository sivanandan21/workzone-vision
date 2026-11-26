#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="/opt/workzone-vision/.env"
BACKUP_DIR="/opt/workzone-vision/backups"
mkdir -p "$BACKUP_DIR"

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

STAMP=$(date -u +%Y%m%d_%H%M%S)
OUT_FILE="${BACKUP_DIR}/workzone_${STAMP}.sql.gz"
docker exec workzone-postgres pg_dump -U "${POSTGRES_USER:-workzone}" "${POSTGRES_DB:-workzone_vision}" | gzip > "$OUT_FILE"
find "$BACKUP_DIR" -name "workzone_*.sql.gz" -mtime +14 -delete
echo "Saved backup: $OUT_FILE"
