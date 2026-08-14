# ============================================
# MindAtlas Backend Dockerfile
# Multi-stage build with non-root user
# ============================================

# Stage 1: Builder - Install dependencies
FROM python:3.11-slim AS builder

ARG APP_BUILD_REVISION=development
ARG API_WORKER_LOCK_SHA256=73f02c14059e0e40994822451b23c94ff15ac3aba95f20251bf1ada6f6c930d9
ARG PARSE_WORKER_LOCK_SHA256=d1846fca87b2242d38782b04f84515b2a9553dd33b7f3e740708397d3bf14419
ARG DEPENDENCY_LOCK_SET_SHA256=4f55315dba8826009556ce50a207e6776a0bca7449aa0fd900dfa521dab5eb35

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install only the reviewed, hash-verified API/Assistant lock.
COPY requirements/api-worker.lock /tmp/requirements/api-worker.lock
RUN python -m pip install --disable-pip-version-check \
      --no-cache-dir --require-hashes \
      -r /tmp/requirements/api-worker.lock \
    && python -m pip check

# Stage 2: Base - Common runtime configuration
FROM python:3.11-slim AS base

# Immutable image/git revision for skill capability publication (override at build).
ARG APP_BUILD_REVISION=development
ARG API_WORKER_LOCK_SHA256=73f02c14059e0e40994822451b23c94ff15ac3aba95f20251bf1ada6f6c930d9
ARG PARSE_WORKER_LOCK_SHA256=d1846fca87b2242d38782b04f84515b2a9553dd33b7f3e740708397d3bf14419
ARG DEPENDENCY_LOCK_SET_SHA256=4f55315dba8826009556ce50a207e6776a0bca7449aa0fd900dfa521dab5eb35

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_BUILD_REVISION=${APP_BUILD_REVISION}

LABEL org.opencontainers.image.revision=${APP_BUILD_REVISION} \
      io.mindatlas.python-version="3.11" \
      io.mindatlas.platform="linux/amd64" \
      io.mindatlas.api-worker-lock-sha256=${API_WORKER_LOCK_SHA256} \
      io.mindatlas.parse-worker-lock-sha256=${PARSE_WORKER_LOCK_SHA256} \
      io.mindatlas.dependency-lock-set-sha256=${DEPENDENCY_LOCK_SET_SHA256}

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --system app && \
    useradd --system --gid app --create-home --home-dir /home/app app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY --chown=app:app . .

# Ensure writable runtime directories.
# Note: when a named volume is mounted to an existing *non-empty* directory, Docker
# will copy the directory content into the empty volume (including ownership).
# We keep a placeholder file here so a fresh `lightrag_storage` volume inherits
# correct permissions for the non-root `app` user.
RUN mkdir -p /app/lightrag_storage && \
    touch /app/lightrag_storage/.keep && \
    chown -R app:app /app/lightrag_storage

USER app

# Stage 3: Migration target
FROM base AS migrate
CMD ["alembic", "upgrade", "head"]

# Stage 4: Runtime target
FROM base AS runtime
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Stage 5: Worker target (LightRAG indexing worker)
FROM base AS worker
CMD ["python", "-m", "app.lightrag.worker"]

# Stage 5b: Durable Main Agent assistant worker (Plan 06)
# Same base image/deps as the API (DB + provider + private MinIO Artifact access).
FROM base AS assistant-worker
ENV ASSISTANT_WORKER_STATE_PATH=/tmp/mindatlas-assistant-worker-state.json
# Healthcheck validates a *fresh compatible registration* (not mere PID liveness).
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-m", "app.assistant.worker", "--healthcheck"]
CMD ["python", "-m", "app.assistant.worker"]

# Stage 6: Parse Worker target (Docling document parsing)
FROM python:3.11-slim AS parse-worker-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements/parse-worker.lock /tmp/requirements/parse-worker.lock
RUN python -m pip install --disable-pip-version-check \
      --no-cache-dir --require-hashes \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r /tmp/requirements/parse-worker.lock \
    && python -m pip check

FROM python:3.11-slim AS parse-worker

ARG APP_BUILD_REVISION=development
ARG PARSE_WORKER_LOCK_SHA256=d1846fca87b2242d38782b04f84515b2a9553dd33b7f3e740708397d3bf14419
ARG DEPENDENCY_LOCK_SET_SHA256=4f55315dba8826009556ce50a207e6776a0bca7449aa0fd900dfa521dab5eb35

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

LABEL org.opencontainers.image.revision=${APP_BUILD_REVISION} \
      io.mindatlas.python-version="3.11" \
      io.mindatlas.platform="linux/amd64" \
      io.mindatlas.parse-worker-lock-sha256=${PARSE_WORKER_LOCK_SHA256} \
      io.mindatlas.dependency-lock-set-sha256=${DEPENDENCY_LOCK_SET_SHA256}

WORKDIR /app

# Install minimal runtime dependencies for parse-worker.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && \
    useradd --system --gid app --create-home --home-dir /home/app app

COPY --from=parse-worker-builder /opt/venv /opt/venv
COPY --chown=app:app app ./app

USER app

CMD ["python", "-m", "app.attachment.worker"]
