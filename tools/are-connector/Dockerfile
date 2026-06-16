FROM python:3.11-slim@sha256:6ed5bff4d7d377e2a27d9285553b8c21cfccc4f00881de1b24c9bc8d90016e82

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CONNECTOR_CONFIG=/var/lib/are-connector/config.json

WORKDIR /opt/are-connector

RUN groupadd --system connector && useradd --system --gid connector --home /var/lib/are-connector connector \
    && mkdir -p /var/lib/are-connector \
    && chown -R connector:connector /var/lib/are-connector

COPY pyproject.toml ./
COPY constraints.txt ./
COPY src ./src
RUN pip install --no-cache-dir --require-hashes -r constraints.txt \
    && SOURCE_DATE_EPOCH=1735689600 pip install --no-cache-dir --no-deps .

USER connector
ENTRYPOINT ["are-connector"]
