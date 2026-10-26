FROM python:3.11-slim
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH=/usr/local/aws-cli/v2/current/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
WORKDIR /app
COPY requirements-runtime.txt ./
RUN python -m pip install --require-hashes -r requirements-runtime.txt
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list.d/debian.sources \
    && python -m playwright install --with-deps chromium && chmod -R a+rX /ms-playwright
COPY . .
RUN useradd --uid 10001 --user-group --create-home --home-dir /home/scanner scanner \
    && chmod -R a+rX /app
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.title="BackBond Amazon Quick pilot worker" \
      org.opencontainers.image.revision="${SOURCE_REVISION}"
USER scanner
# Build-time contract check: scripted outcomes, no AWS/judge/network calls.
RUN python deploy/amazon-quick/verify-fixtures.py
ENTRYPOINT ["python", "-m"]
CMD ["tools.quick_pilot_operations", "runtime-check"]
