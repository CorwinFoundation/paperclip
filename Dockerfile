# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12-slim

FROM python:${PYTHON_VERSION} AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements-runtime.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --upgrade pip \
    && /opt/venv/bin/python -m pip install -r requirements-runtime.txt

COPY . .
RUN /opt/venv/bin/python scripts/verify_runtime_import_closure.py

FROM python:${PYTHON_VERSION} AS runtime

ARG GIT_SHA=unknown

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BACKBOND_BUILD_SHA="${GIT_SHA}" \
    ARE_SCAN_LOG_PATH=/var/lib/are-scanner/scan_log.sqlite3

LABEL org.opencontainers.image.title="BackBond Scanner live edge" \
      org.opencontainers.image.description="FastAPI live scanner edge image; state and secrets are host-mounted." \
      org.opencontainers.image.revision="${GIT_SHA}"

WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app

RUN useradd --system --create-home --home-dir /home/scanner scanner \
    && mkdir -p /var/lib/are-scanner \
    && chown -R scanner:scanner /var/lib/are-scanner /home/scanner

USER scanner

VOLUME ["/var/lib/are-scanner"]
EXPOSE 8077

ENTRYPOINT ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8077"]
