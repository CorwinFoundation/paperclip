# BackBond Passive Monitor appliance. Runtime dependencies are sealed in-image.
FROM python:3.11-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff

ARG APPLIANCE_VERSION=0.1.3
LABEL org.opencontainers.image.title="BackBond Passive Monitor appliance" \
      org.opencontainers.image.version="${APPLIANCE_VERSION}" \
      org.opencontainers.image.vendor="BackBond" \
      org.opencontainers.image.licenses="Proprietary" \
      ai.backbond.appliance.version="${APPLIANCE_VERSION}"

# Non-root user; the appliance only ever needs READ access to the mounted substrate.
RUN useradd -r -u 10001 -m backbond
WORKDIR /opt/backbond-appliance

COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY appliance/ ./appliance/
COPY server/ ./server/
COPY synthetic/ ./synthetic/
COPY tests/ ./tests/
COPY config.example.yaml ./config.example.yaml
COPY run_all_tests.sh ./run_all_tests.sh
COPY Dockerfile install.sh compose.yaml README.md INSTALL.md backbond-appliance.service ./
COPY release/sbom.spdx.json ./release/sbom.spdx.json

RUN chmod 0555 ./run_all_tests.sh \
    && mkdir -p /home/backbond/.backbond \
    && chown -R backbond:backbond /home/backbond

USER backbond
ENV BACKBOND_CONFIG=/config/config.yaml

HEALTHCHECK --interval=60s --timeout=15s --start-period=15s --retries=3 \
  CMD ["python", "-m", "appliance.main", "selftest", "-c", "/config/config.yaml"]

# Mounts expected at runtime:
#   -v /path/to/agent/activity:/data:ro      (the substrate, READ-ONLY)
#   -v /path/to/config:/config:ro
#   -v backbond-state:/home/backbond/.backbond   (keys, chain state, outbox)
ENTRYPOINT ["python", "-m", "appliance.main"]
CMD ["run"]
