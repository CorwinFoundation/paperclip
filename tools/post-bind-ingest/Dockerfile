FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BACKBOND_EGRESS_ENABLED=false

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

RUN useradd --create-home --shell /usr/sbin/nologin monitor
USER monitor

VOLUME ["/input", "/output"]
ENTRYPOINT ["post-bind-ingest"]
CMD ["/input/receiver_payload.json", "--jwt-public-key", "change-me", "--output-path", "/output/normalized-events.jsonl"]
