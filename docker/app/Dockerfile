FROM python:3.12-slim

# Install system dependencies required by psycopg binary and cryptography.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for fast dependency installation.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency manifests first for layer caching.
COPY pyproject.toml uv.lock ./

# Install production dependencies only (no dev extras).
RUN uv sync --frozen --no-dev

# Copy application source.
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Copy Docker entrypoint script.
COPY docker/app/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
