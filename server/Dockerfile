# Builder layer with uv
FROM python:3.12-slim AS builder

# Install uv (much faster than pip)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first (better layer caching)
COPY server/pyproject.toml server/uv.lock ./

# Install dependencies into a virtual environment
# --frozen: don't update lock file
# --no-dev: skip dev dependencies
ENV UV_LINK_MODE=copy

RUN uv sync --frozen --no-dev --no-install-project

# Download NLTK data required by pipecat
RUN .venv/bin/python -c "import nltk; nltk.download('punkt_tab', download_dir='/opt/nltk_data')"

# ============================================
# Runtime layer
# ============================================
FROM python:3.12-slim

WORKDIR /usr/src/app

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser && \
    chown -R appuser:appuser /usr/src/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /build/.venv /usr/src/app/.venv
COPY --from=builder /opt/nltk_data /opt/nltk_data

# Copy application code
COPY --chown=appuser:appuser server/ .

ENV PATH="/usr/src/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NLTK_DATA=/opt/nltk_data \
    WORKERS="1" \
    TIMEOUT="360"

USER appuser
EXPOSE 8100

# Run gunicorn via python -m (avoids shebang path issues)
CMD ["sh", "-c", "/usr/src/app/.venv/bin/python -m gunicorn app.main:app --workers $WORKERS --worker-class uvicorn.workers.UvicornWorker --timeout $TIMEOUT --bind 0.0.0.0:${PORT:-8100} --access-logfile - --error-logfile - --log-level info"]
