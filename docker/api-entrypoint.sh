#!/usr/bin/env sh
#
# Container entrypoint for the Notes API.
#
# Responsibilities, in order:
#   1. wait until the database accepts connections (bounded, with backoff);
#   2. apply Alembic migrations;
#   3. optionally seed the demo users (idempotent);
#   4. hand off to uvicorn as PID 1 via exec.
#
# Everything is driven by environment variables so the same image works against
# SQLite or PostgreSQL and in development or production.
set -eu

: "${DATABASE_URL:?DATABASE_URL must be set}"
PORT="${PORT:-8000}"
SEED_DEMO_USERS="${SEED_DEMO_USERS:-true}"
DB_WAIT_TIMEOUT_SECONDS="${DB_WAIT_TIMEOUT_SECONDS:-60}"

echo "[entrypoint] environment=${ENVIRONMENT:-development} provider=${DATABASE_PROVIDER:-sqlite}"

echo "[entrypoint] waiting for database (timeout ${DB_WAIT_TIMEOUT_SECONDS}s)..."
python - "$DB_WAIT_TIMEOUT_SECONDS" <<'PY'
import os
import sys
import time

from sqlalchemy import create_engine, text

timeout = float(sys.argv[1])
url = os.environ["DATABASE_URL"]
deadline = time.monotonic() + timeout
last_error: Exception | None = None

while time.monotonic() < deadline:
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        print("[entrypoint] database is reachable")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001 - any connection failure means "retry"
        last_error = exc
        time.sleep(2)

print(f"[entrypoint] database not reachable after {timeout:.0f}s: {last_error}", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] applying database migrations..."
alembic upgrade head

if [ "$SEED_DEMO_USERS" = "true" ]; then
    echo "[entrypoint] seeding demo users (idempotent)..."
    notes-service seed
fi

echo "[entrypoint] starting API on 0.0.0.0:${PORT}"
exec notes-service serve --host 0.0.0.0 --port "$PORT"
