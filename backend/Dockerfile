# ============================================
# MindAtlas Backend Dockerfile
# Multi-stage build with non-root user
# ============================================

# Stage 1: Builder - Install dependencies
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Base - Common runtime configuration
FROM python:3.11-slim AS base

# Immutable image/git revision for skill capability publication (override at build).
ARG APP_BUILD_REVISION=development

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_BUILD_REVISION=${APP_BUILD_REVISION}

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

# Stage 6: Parse Worker target (Docling document parsing)
FROM python:3.11-slim AS parse-worker-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG TORCH_CPU_VERSION=2.7.0
ARG TORCHVISION_CPU_VERSION=0.22.0

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements-docling.txt requirements-parse-worker.txt ./
RUN pip install --no-cache-dir \
        "torch==${TORCH_CPU_VERSION}" \
        "torchvision==${TORCHVISION_CPU_VERSION}" \
        --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements-parse-worker.txt

FROM python:3.11-slim AS parse-worker

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

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
