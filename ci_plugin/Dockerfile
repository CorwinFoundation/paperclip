# BackBond CI/CD plugin — one container entrypoint shared by the GitHub
# Action and the GitLab CI template (WS-5, BEAAA-11729).
#
# Build context MUST be the repository root so the shipped scanner modules
# the plugin reuses (wrap, don't fork) are available:
#     docker build -f ci_plugin/Dockerfile -t are-scanner-action:local .
#
# The plugin is stdlib-only: no pip install step, no wheels, no probe corpus.
FROM python:3.12-slim

WORKDIR /plugin

# Reuse the SHIPPED scanner modules (HMAC + infra/score taxonomy) rather than
# re-implementing them. Only the two stdlib-only modules the plugin imports
# are copied — not the FastAPI app.
COPY api/__init__.py       /plugin/api/__init__.py
COPY api/jcs.py            /plugin/api/jcs.py
COPY api/ci_scan_ext.py    /plugin/api/ci_scan_ext.py
COPY ci_plugin/            /plugin/ci_plugin/

ENV PYTHONPATH=/plugin PYTHONUNBUFFERED=1
# The entrypoint returns the gate exit code (0 pass / 1 fail / 3 prod-refuse /
# 4 HMAC / 75 infra-advisory) — the CI runner reads it directly.
ENTRYPOINT ["python", "-m", "ci_plugin.runner"]
