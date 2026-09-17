# APIX LLMOps ops-console backend. Reads the shared JSONL sinks (mounted at
# /data) into a SQLite mirror and serves aggregations. No ODBC (no direct SQL).
# BUILD CONTEXT = repo root (platform/ included for /api/prompts + promote):
#   docker build -f ops-console/backend/Dockerfile -t apix-ops-backend .
FROM python:3.11-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLMOPS_PLATFORM_ROOT=/app/platform \
    PORT=8100

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY ops-console/backend/requirements.txt /app/ops-console/backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/ops-console/backend/requirements.txt

COPY platform/ /app/platform/
COPY ops-console/backend/ /app/ops-console/backend/

WORKDIR /app/ops-console/backend
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8100}/healthz || exit 1
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8100}"]
