# APIX combined container — application (dashboard API + React SPA), chatbot,
# and the pipeline trigger API, all in ONE Container App, routed by nginx on a
# single exposed port.
#
# Each app keeps its OWN Python virtual environment. This is NOT optional
# packaging — the four apps' frozen dependency sets have real, unresolvable
# version conflicts if merged into one environment (verified empirically:
# aiohappyeyeballs==2.6.1 vs ==2.6.2, and pip's resolver stops enumerating after
# the first conflict, so there are likely more). Separate venvs mean nothing
# about any app's tested runtime changes — only how they're packaged and exposed.
#
# BUILD CONTEXT = repo root:
#   docker build -f usecases/apix/combined/Dockerfile -t apix-combined .

# ---- Stage 1: build the dashboard React SPA --------------------------------
FROM node:20-alpine AS web-build
WORKDIR /web
COPY usecases/apix/application/web/package.json usecases/apix/application/web/package-lock.json* ./
RUN npm install
COPY usecases/apix/application/web/ ./
# Same-origin (nginx serves the SPA and proxies /api, /chatbot, /pipeline all
# from one origin) — no absolute API URLs need to be baked in at build time.
ENV VITE_DASHBOARD_API_URL="" VITE_CHAT_API_URL=""
RUN npm run build

# ---- Stage 2: runtime --------------------------------------------------------
FROM python:3.11-slim-bookworm

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LLMOPS_PLATFORM_ROOT=/app/platform

# ODBC Driver 18 (chatbot + dashboard-api + pipeline all read Azure SQL via
# managed identity) + nginx + supervisor (runs nginx + the 3 uvicorn processes
# under one PID 1). Bookworm pinned on purpose — Microsoft's ODBC repo is
# SHA1-signed and trixie's apt rejects that.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates unixodbc-dev nginx supervisor \
    && curl -sSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb && rm packages-microsoft-prod.deb \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

# Shared platform (PYTHONPATH src-layout — each app's llmops/ adapter finds
# this by walking up to /app/platform; read by all three apps, installed once).
COPY platform/ /app/platform/

# --- dashboard (backend + api), its own venv ---
COPY usecases/apix/application/ /app/application/
RUN python -m venv /opt/venv-dashboard && \
    /opt/venv-dashboard/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv-dashboard/bin/pip install --no-cache-dir \
        -r /app/application/requirements.txt \
        -r /app/application/api/requirements.txt \
        pyyaml
# Built SPA static files, served directly by nginx — no proxy for the UI itself.
COPY --from=web-build /web/dist /app/application/web/dist

# --- chatbot, its own venv ---
COPY usecases/apix/chatbot/ /app/chatbot/
RUN python -m venv /opt/venv-chatbot && \
    /opt/venv-chatbot/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv-chatbot/bin/pip install --no-cache-dir \
        -r /app/chatbot/requirements.txt \
        pyyaml

# --- pipeline (+ its trigger API), its own venv ---
COPY usecases/apix/ai_pipeline/ /app/ai_pipeline/
RUN python -m venv /opt/venv-pipeline && \
    /opt/venv-pipeline/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv-pipeline/bin/pip install --no-cache-dir \
        -r /app/ai_pipeline/requirements.txt \
        -r /app/ai_pipeline/api/requirements.txt \
        pyyaml && \
    /opt/venv-pipeline/bin/pip install --no-cache-dir -e /app/ai_pipeline

# nginx: replace the Debian default site with the APIX routing config.
RUN rm -f /etc/nginx/sites-enabled/default
COPY usecases/apix/combined/nginx.conf /etc/nginx/conf.d/apix.conf
COPY usecases/apix/combined/supervisord.conf /etc/supervisor/conf.d/apix.conf

# Runs as root: supervisord manages 4 processes (nginx + 3 uvicorn workers) and
# nginx needs to bind low-ish ports; simplest for this dev-stage image — revisit
# if this becomes a hardening concern.

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/apix.conf"]
