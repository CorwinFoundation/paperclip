# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.11-slim

FROM python:${PYTHON_VERSION} AS build

ARG GIT_SHA=unknown

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements-runtime.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --require-hashes -r requirements-runtime.txt \
    && /opt/venv/bin/python -c "import psycopg"

COPY . .
RUN /opt/venv/bin/python scripts/render_release_manifest.py \
      --build-sha "${GIT_SHA}" \
      --output release/backbond-scanner-release.json
RUN /opt/venv/bin/python scripts/verify_runtime_import_closure.py

FROM python:${PYTHON_VERSION} AS runtime

ARG GIT_SHA=unknown

ENV PATH="/opt/venv/bin:${PATH}" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BACKBOND_BUILD_SHA="${GIT_SHA}" \
    ARE_SCAN_LOG_PATH=/var/lib/are-scanner/scan_log.sqlite3 \
    BACKBOND_SCAN_TENANT_ID=ariclaw \
    BACKBOND_SCAN_AGENT_ENDPOINT=https://api.vapi.ai \
    BACKBOND_SCAN_EVIDENCE_ROOT=/var/lib/are-scanner/scheduled-scans \
    BACKBOND_SCAN_SCANNER_VERSION=0.5.0 \
    BACKBOND_SCAN_CORPUS_VERSION=0.2.0 \
    BACKBOND_SCAN_SEED=0 \
    BACKBOND_SCAN_CONCURRENCY=1 \
    BACKBOND_SCAN_RPM=30 \
    BACKBOND_SCAN_RETRIES=0 \
    BACKBOND_SCAN_COST_CAP_USD=100 \
    BACKBOND_SCAN_TIMEOUT_SECONDS=21600 \
    BACKBOND_SCAN_JUDGE_MODEL=anthropic/claude-haiku-4.5

LABEL org.opencontainers.image.title="BackBond Scanner live edge" \
      org.opencontainers.image.description="FastAPI live scanner edge image; state and secrets are host-mounted." \
      org.opencontainers.image.source="https://github.com/CorwinFoundation/are-scanner" \
      org.opencontainers.image.revision="${GIT_SHA}"

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app

RUN /opt/venv/bin/python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

RUN useradd --system --create-home --home-dir /home/scanner scanner \
    && mkdir -p /var/lib/are-scanner \
    && chown -R scanner:scanner /var/lib/are-scanner /home/scanner

USER scanner

VOLUME ["/var/lib/are-scanner"]
EXPOSE 8077

ENTRYPOINT ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8077"]
