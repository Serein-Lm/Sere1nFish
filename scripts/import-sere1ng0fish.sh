#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
BACKUP="${1:-$ROOT_DIR/server/backup/Sere1nG0Fish/db_20260701_160002.json}"

cd "$ROOT_DIR/server"

set -- python -m scripts.db_migrate import \
  --input "$BACKUP" \
  --target-uri "${MONGODB_URI:-mongodb://127.0.0.1:27017}" \
  --target-db "${MONGODB_DATABASE:-Sere1nG0Fish}" \
  --target-auth-source "${MONGODB_AUTH_SOURCE:-admin}" \
  --drop-before-import

if [ -n "${MONGODB_USERNAME:-}" ]; then
  set -- "$@" --target-user "$MONGODB_USERNAME"
fi

if [ -n "${MONGODB_PASSWORD:-}" ]; then
  set -- "$@" --target-pass "$MONGODB_PASSWORD"
fi

if [ -n "${MONGODB_DIRECT:-}" ]; then
  set -- "$@" --target-direct
fi

exec "$@"
