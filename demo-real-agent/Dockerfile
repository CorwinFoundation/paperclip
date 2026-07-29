FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DEMO_ENVIRONMENT=container

WORKDIR /app

COPY api /app/api
COPY harness /app/harness
COPY tests/probe_sets /app/tests/probe_sets
COPY artifacts/beaaa-152/datasets/halueval_dialogue_summ_general_n300.json /app/artifacts/beaaa-152/datasets/halueval_dialogue_summ_general_n300.json
COPY artifacts/beaaa-18720/unified-probe-inventory.jsonl /app/artifacts/beaaa-18720/unified-probe-inventory.jsonl
COPY rubric /app/rubric
COPY scanners /app/scanners
COPY release /app/release
COPY release_identity.py /app/release_identity.py
COPY tools/content_candidate_inventory.py /app/tools/content_candidate_inventory.py
COPY tools/active_test_registry.py /app/tools/active_test_registry.py
COPY tools/active_test_registry_claims.json /app/tools/active_test_registry_claims.json
COPY tools/framework_worker_registry.py /app/tools/framework_worker_registry.py
COPY tools/framework_workers.py /app/tools/framework_workers.py
COPY tools/registered_case_executor.py /app/tools/registered_case_executor.py
COPY tools/text_case_materializer.py /app/tools/text_case_materializer.py
COPY demo-real-agent /app/demo-real-agent

RUN python -m pip install --no-cache-dir PyYAML==6.0.2 httpx==0.27.2 anthropic==0.84.0 \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin demo \
    && mkdir -p /data/runs /etc/backbond \
    && chown -R demo:demo /data

USER 10001:10001
EXPOSE 8765
VOLUME ["/data/runs"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import json,urllib.request; h=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=2)); assert h['status']=='ok' and h['runnable_case_count']==203 and h['synthetic'] is False"

CMD ["python", "demo-real-agent/app.py", "--host", "0.0.0.0", "--port", "8765", "--runs-dir", "/data/runs"]
