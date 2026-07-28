#!/bin/sh
set -eu

alembic upgrade head
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${SERVER_PORT:-9090}" \
  --workers "${WEB_WORKERS:-2}" \
  --proxy-headers
