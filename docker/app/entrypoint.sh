#!/bin/sh
# Application entrypoint script.
# Runs Alembic migrations BEFORE starting the FastAPI or Celery process.
# The CMD is passed through from docker-compose.

set -e

echo "Running Alembic migrations..."
uv run alembic upgrade head

echo "Starting process: $*"
exec uv run "$@"
