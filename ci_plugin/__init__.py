"""BackBond OSS CI/CD plugin (WS-5, BEAAA-11729).

A thin, standalone component that wraps the **shipped** BackBond Scanner
routes so a vendor can gate a CI/CD pipeline on their agent's BackBond Score:

* ``POST /scanner/v1/scans`` (with ``scan_tier``) → ``scan_id``
* ``GET  /scan/{scan_id}``  → ``{are_total, band}``
* HMAC-SHA256 completion webhook verification (``X-BackBond-Signature``)

Two surfaces share one container entrypoint:

* GitHub Action ``backbond/are-scanner-action@v1`` (``action.yml``)
* GitLab CI include template (``templates/.gitlab-ci.yml``)

The plugin ships **no probe corpus** — it speaks only the result contract
(WS-1 anti-gaming preserved). It reuses ``api.ci_scan_ext`` for HMAC and the
infra-vs-score taxonomy rather than re-implementing them (wrap, don't fork).

Design authority: WS-5 frozen design on BEAAA-11715; contract
``are-scanner-api-v1`` v1.7.0 (WS-4 / BEAAA-11714).

**Internal-only.** External publish to GitHub Marketplace / public GitLab
template is a separate, gated CTO action (BEAAA-11755) requiring the CEO
release gate + Founder confirmation on BEAAA-11710. This package never
publishes itself.
"""
from ci_plugin.gate import (
    EXIT_GATE_FAIL,
    EXIT_HMAC_MISMATCH,
    EXIT_INFRA_ADVISORY,
    EXIT_PASS,
    EXIT_PRODUCTION_REFUSED,
    GateDecision,
    decide_exit,
    resolve_threshold,
)

__all__ = [
    "EXIT_PASS",
    "EXIT_GATE_FAIL",
    "EXIT_PRODUCTION_REFUSED",
    "EXIT_HMAC_MISMATCH",
    "EXIT_INFRA_ADVISORY",
    "GateDecision",
    "decide_exit",
    "resolve_threshold",
]
