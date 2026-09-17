# APIX dashboard backend image — serves the FastAPI API (default) that the React
# SPA consumes. The legacy Streamlit UI can still be run from the same image by
# overriding the command. Deploys as a long-running Container App.
# BUILD CONTEXT = repo root (so platform/ is included):
#   docker build -f usecases/apix/application/Dockerfile -t apix-dashboard-api .
FROM python:3.11-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/application \
    LLMOPS_PLATFORM_ROOT=/app/platform \
    PORT=8000

# ODBC Driver 18 — backend/services/azure_sql*.py read Azure SQL via pyodbc.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates unixodbc-dev \
    && curl -sSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb && rm packages-microsoft-prod.deb \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

# App deps + the API's own deps (FastAPI/PyJWT/msal).
COPY usecases/apix/application/requirements.txt /app/application/requirements.txt
COPY usecases/apix/application/api/requirements.txt /app/application/api/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/application/requirements.txt \
                -r /app/application/api/requirements.txt pyyaml

COPY platform/ /app/platform/
COPY usecases/apix/application/ /app/application/

WORKDIR /app/application
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1
# Default = the dashboard REST API. Legacy Streamlit override:
#   streamlit run main.py --server.address=0.0.0.0 --server.port=${PORT:-8501}
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
