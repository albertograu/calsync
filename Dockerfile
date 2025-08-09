# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

# System deps
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      tzdata \
      git \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md /app/
COPY src /app/src

# Install project
RUN pip install --upgrade pip setuptools wheel && \
    pip install .

# Create runtime dirs (can be overridden by env mounts)
RUN mkdir -p /data /credentials

# Default environment (override via docker-compose or -e)
ENV DATA_DIR=/data \
    CREDENTIALS_DIR=/credentials \
    LOG_LEVEL=INFO \
    SYNC_CONFIG__SYNC_INTERVAL_MINUTES=30 \
    SYNC_CONFIG__MAX_EVENTS_PER_SYNC=1000 \
    SYNC_CONFIG__SYNC_PAST_DAYS=30 \
    SYNC_CONFIG__SYNC_FUTURE_DAYS=365

# Entrypoint wraps the CLI
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["daemon"]


