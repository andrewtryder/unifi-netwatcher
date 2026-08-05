#!/usr/bin/env sh
set -eu

echo "Running database migrations..."
alembic upgrade head

echo "Starting NetWatcher..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
