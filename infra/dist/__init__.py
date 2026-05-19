"""Design-partner distribution scaffolding (BEAAA-567 / ADR-0011).

This package owns the PyInstaller build for Path 3 — the design-partner
single-file binary. It is the *only* code path that wraps the scanner in a
license-key gate; `bin/are-scan` and the in-agent scoring stack remain
gate-free so calibration stays byte-identical across distribution modes.

Modules:

* :mod:`infra.dist.license_gate` — fail-closed runtime gate that calls the
  IFA license-validation endpoint (BEAAA-569) on every invocation.
* :mod:`infra.dist.are_scan_dist` — PyInstaller entry point. Runs the gate,
  enforces EULA acceptance, then delegates to ``bin/are-scan``'s ``main()``.
* :mod:`infra.dist.build` — deterministic build orchestrator (per-partner
  salt, corpus watermark, manifest emission, sha256 of binary).

Out of scope here (separate child issues):

* macOS builds (S6 child — needs a macOS build host).
* Calibration smoke (S4 child).
* Pinning survival proof (S3 child).
* Negative-smoke harness (S5 child).
"""
