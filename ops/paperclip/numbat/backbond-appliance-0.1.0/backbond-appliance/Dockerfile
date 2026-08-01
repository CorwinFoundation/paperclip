# BackBond drop-in appliance — minimal, read-only by intent.
FROM python:3.11-slim

# Non-root user; the appliance only ever needs READ access to the mounted substrate.
RUN useradd -r -u 10001 -m backbond
WORKDIR /opt/backbond-appliance

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY appliance/ ./appliance/
COPY config.example.yaml ./config.example.yaml

USER backbond
ENV BACKBOND_CONFIG=/config/config.yaml

# Mounts expected at runtime:
#   -v /path/to/agent/activity:/data:ro      (the substrate, READ-ONLY)
#   -v /path/to/config:/config:ro
#   -v backbond-state:/home/backbond/.backbond   (keys, chain state, outbox)
ENTRYPOINT ["python", "-m", "appliance.main"]
CMD ["run"]
