# APIX ai_pipeline — batch job image. Deploys as an Azure Container Apps Job.
# BUILD CONTEXT = repo root (so platform/ is included):
#   docker build -f usecases/apix/ai_pipeline/Dockerfile -t apix-pipeline .
# Bookworm pinned on purpose (Microsoft ODBC repo is SHA1-signed; trixie apt rejects it).
FROM python:3.11-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    LLMOPS_PLATFORM_ROOT=/app/platform

# ODBC Driver 18 — the individual_metrics step reads Azure SQL via pyodbc.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates unixodbc-dev \
    && curl -sSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb && rm packages-microsoft-prod.deb \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

COPY usecases/apix/ai_pipeline/requirements.txt /app/ai_pipeline/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/ai_pipeline/requirements.txt pyyaml

# Platform (consumed via PYTHONPATH src-layout) + the pipeline package.
COPY platform/ /app/platform/
COPY usecases/apix/ai_pipeline/ /app/ai_pipeline/

# CMD FIXED (was `etl.main`). A Container Apps Job overrides args per run, e.g.
#   --mode telesales --start 2025-08-01 --end 2025-08-07
CMD ["python", "-m", "ai_pipeline.main", "--program", "telesales"]
