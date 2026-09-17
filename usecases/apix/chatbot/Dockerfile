# APIX Chatbot API. Deploys as a long-running Container App (+ an ingestion Job).
# BUILD CONTEXT = repo root (so platform/ is included):
#   docker build -f usecases/apix/chatbot/Dockerfile -t apix-chatbot .
FROM python:3.11-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    LLMOPS_PLATFORM_ROOT=/app/platform \
    PORT=8000

# ODBC Driver 18 — chatbot/sources/azure_source.py reads Azure SQL via pyodbc.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates unixodbc-dev \
    && curl -sSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb && rm packages-microsoft-prod.deb \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

COPY usecases/apix/chatbot/requirements.txt /app/chatbot/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/chatbot/requirements.txt pyyaml

COPY platform/ /app/platform/
COPY usecases/apix/chatbot/ /app/chatbot/

WORKDIR /app/chatbot
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1
# Long-running API. For the weekly ingestion Job, override:
#   python -m chatbot.ingestion
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
