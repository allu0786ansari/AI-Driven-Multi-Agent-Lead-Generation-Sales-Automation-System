# Lead desk: backend (FastAPI + worker) and the built dashboard in one image.
#
#   docker compose up -d --build        (see docs/docker.md)
#
# Base images are build arguments so they can be pinned or mirrored.
ARG NODE_IMAGE=node:24-slim
ARG PYTHON_IMAGE=python:3.12-slim

# ---- 1. Build the dashboard ------------------------------------------------
FROM ${NODE_IMAGE} AS dashboard
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. Runtime --------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN python3 -m venv /opt/venv
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/ ./
COPY --from=dashboard /build/frontend/dist /app/frontend/dist

# Runs as an ordinary user; everything it writes goes to /data (a volume).
RUN useradd --uid 10001 --user-group --home-dir /app --no-create-home leadgen \
    && mkdir -p /data /exports /app/backend/data/eval/reports \
    && chown -R leadgen:leadgen /data /exports /app/backend/data

ENV APP_ENV=production \
    DATABASE_URL=sqlite:////data/app.db \
    SHEETS_CSV_PATH=/data/leads_mirror.csv \
    BACKUP_DIR=/data/backups \
    FRONTEND_DIST=/app/frontend/dist \
    LOG_JSON=true \
    PORT=8000

USER leadgen
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

# Migrations, first account and catalog (if missing), then the server.
CMD ["python", "-m", "scripts.bootstrap", "serve"]
