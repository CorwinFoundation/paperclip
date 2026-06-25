#!/usr/bin/env python3
"""BEAAA-5414 — AG-B6 emission-path N=5 calibration harness.

Per ADR-0030 §Acceptance Gates AG-B6 (CTO amendment 1 via BEAAA-5418):

    N=5 calibration spread on synthetic_v0 fallback path
    (``ARE_WALKER_CUSTOMER_AGENT=0``) against bec8f80 baseline:
    spread MUST = 0 (rollback determinism gate). The harness
    MUST execute under ``ARE_PRODUCTION_SCAN_WIRING=1`` against the
    real ``api/scan_runtime_factory.make_default_production_scan_runtime``
    path — NOT a monkey-patched in-process variant. The harness
    includes an explicit emission-path guard that raises
    ``RuntimeError`` when ``os.getenv("ARE_PRODUCTION_SCAN_WIRING")
    != "1"``. Addresses the BEAAA-2316 B2 calibration blindspot where
    monkey-patched ``pab.FIXTURE_CONFIG`` produced false spread=0.

What this harness does
----------------------

1. **Emission-path guard** — refuses to run unless
   ``ARE_PRODUCTION_SCAN_WIRING=1`` is set on the parent process
   environment AT IMPORT TIME. Raises ``RuntimeError`` with the exact
   amendment-1 message so the failure is unambiguous to CI.
2. **Explicit selector pin** — sets
   ``ARE_WALKER_CUSTOMER_AGENT=0`` (synthetic_v0 fallback) for the
   duration of the calibration; the AG-B6 baseline is rollback
   determinism on the synthetic path, not the customer-true path.
3. **Real factory invocation** — calls
   :func:`api.scan_runtime_factory.make_default_production_scan_runtime`
   and drives the returned ``ScanRuntime`` N=5 times via
   ``runtime.run_scan_fn(scan_request, secret)``. The Prober + Scorer
   subprocesses are spawned for every replicate; the fork-inherited
   vault carries the credential into the Prober; the score envelope
   is drained from the API-edge registry.
4. **Spread gate** — asserts ``are_total`` spread = 0 and every
   subscore spread = 0 across the 5 replicates. Also asserts the
   canonical JCS-form envelope set has cardinality = 1 (byte-stable
   serialization).
5. **Artifacts** — emits
   ``tools/calibration-deltas/<date>-beaaa5414-track-b-walker-n5.json``
   carrying every replicate's envelope + the spread summary, so the
   CTO merge co-sign can recompute spread without re-running.

Usage::

    ARE_PRODUCTION_SCAN_WIRING=1 python3 scripts/beaaa5414_track_b_calibration.py

Exit 0 on AG-B6 PASS (spread=0, cardinality=1); exit 1 on FAIL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Emission-path guard — MUST run before any factory import that could
# observe ARE_PRODUCTION_SCAN_WIRING. Raises RuntimeError with the exact
# amendment-1 message so the failure is unambiguous to CI logs.
# ---------------------------------------------------------------------------


def _enforce_emission_path_guard() -> None:
    """BEAAA-5414 AG-B6 / CTO amendment 1 — refuse to run unless the
    parent process explicitly sets ``ARE_PRODUCTION_SCAN_WIRING=1``.

    Closes the BEAAA-2316 B2 calibration blindspot where
    monkey-patched ``pab.FIXTURE_CONFIG`` produced false spread=0:
    that harness ran in-process against a fake factory; the real
    factory path was never exercised. The merge co-sign now binds on
    a calibration that PROVES it touched the real factory by gating
    on this env var (the production wiring branch of
    ``api/app.py::create_app`` keys on the same env var).
    """
    if os.getenv("ARE_PRODUCTION_SCAN_WIRING") != "1":
        raise RuntimeError(
            "BEAAA-5414 AG-B6 emission-path guard: "
            "ARE_PRODUCTION_SCAN_WIRING must equal '1' to run this "
            "calibration harness. The AG-B6 gate is rollback "
            "determinism on the REAL "
            "make_default_production_scan_runtime path; running this "
            "harness without the env var would silently exercise a "
            "different code path (the BEAAA-2316 B2 calibration "
            "blindspot per CTO amendment 1 via BEAAA-5418)."
        )


_enforce_emission_path_guard()

# Pin synthetic_v0 fallback for the AG-B6 baseline. The customer-true
# path is non-deterministic by construction (real customer endpoints);
# AG-B6 gates rollback determinism, which is the synthetic path.
os.environ["ARE_WALKER_CUSTOMER_AGENT"] = "0"


from api.scan_runtime_factory import (  # noqa: E402
    ARE_WALKER_CUSTOMER_AGENT_ENV,
    _customer_agent_walker_enabled,
    build_v0_corpus_tarball,
    make_default_production_scan_runtime,
    v0_corpus_snapshot_hash,
)
from scanners.live_probe_orchestrator import ScanRequest  # noqa: E402


# Sanity-check the selector evaluation matches what we expect.
assert _customer_agent_walker_enabled() is False, (
    f"AG-B6 baseline pin failed — {ARE_WALKER_CUSTOMER_AGENT_ENV} should "
    "resolve to False but doesn't. Did another caller mutate the env?"
)


# ---------------------------------------------------------------------------
# Replicate driver
# ---------------------------------------------------------------------------


_FIXTURE_AGENT_ENDPOINT: str = "https://agb6-fixture.invalid/scan"
_FIXTURE_CREDENTIAL_REF: str = "cred-agb6-fixture"
_FIXTURE_SECRET: str = "AGB6-FIXTURE-SECRET-NOT-A-REAL-CREDENTIAL"
_FIXTURE_TENANT_ID: str = "tenant-agb6"
_FIXTURE_SCAN_ID_TEMPLATE: str = "scan-agb6-{replicate:02d}"
_FIXTURE_AGENT_ID: str = "agent-agb6-baseline"
_FIXTURE_SCANNER_VERSION: str = "0.4.2"
_FIXTURE_CORPUS_VERSION: str = "0.1.0"
_FIXTURE_SEED: int = 42
_FIXTURE_PURPOSE: str = "pre_bind"


def _build_scan_request(replicate_idx: int, corpus_snapshot_hash: str) -> ScanRequest:
    return ScanRequest(
        scan_id=_FIXTURE_SCAN_ID_TEMPLATE.format(replicate=replicate_idx),
        tenant_id=_FIXTURE_TENANT_ID,
        agent_endpoint=_FIXTURE_AGENT_ENDPOINT,
        credential_ref=_FIXTURE_CREDENTIAL_REF,
        corpus_snapshot_hash=corpus_snapshot_hash,
        scanner_version=_FIXTURE_SCANNER_VERSION,
        corpus_version=_FIXTURE_CORPUS_VERSION,
        seed=_FIXTURE_SEED,
        agent_id=_FIXTURE_AGENT_ID,
    )


def _drive_one_replicate(
    runtime, replicate_idx: int, corpus_snapshot_hash: str
) -> dict[str, object]:
    """Run a single fork-spawn end-to-end against the real factory and
    return the score envelope dict pulled from the API-edge registry.

    Each replicate constructs its own ``ScanRequest`` (distinct
    ``scan_id`` so the prior-scan-refs registry isn't tripped) and
    calls ``runtime.run_scan_fn`` synchronously — the call returns
    after the Prober + Scorer subprocesses join.
    """
    scan_request = _build_scan_request(replicate_idx, corpus_snapshot_hash)

    # ScanRuntime stores the kickoff callable as ``_run_scan_fn``; that's
    # the entrypoint the API edge invokes inside ``kickoff()``. Calling
    # it directly lets us drain the score envelope without persisting
    # a scan_log row (the calibration is read-only on the wire surface).
    result = runtime._run_scan_fn(scan_request, _FIXTURE_SECRET)

    # The factory closure on the API edge captures the envelope into
    # an internal registry keyed by scan_id; the Scorer subprocess
    # has already projected it. We pull it back via the runtime's
    # score_envelope_factory closure to mimic what the API-edge
    # response handler does.
    factory = getattr(runtime, "_score_envelope_factory", None)
    if factory is None:
        raise RuntimeError(
            "make_default_production_scan_runtime() returned a runtime "
            "without the _score_envelope_factory closure attached. "
            "ARE_SCORER_REAL must be 'on' for the AG-B6 calibration."
        )
    return factory(
        result,
        _FIXTURE_AGENT_ID,
        "2026-06-15T08:00:00Z",
    )


# ---------------------------------------------------------------------------
# Spread / canonicalization
# ---------------------------------------------------------------------------


def _canonical_json(envelope: dict[str, object]) -> str:
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"))


def _spread(envelopes: list[dict[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    are_totals = [env["are_total"] for env in envelopes]
    if any(t == "_phase1_pending" for t in are_totals):
        out["are_total"] = "_phase1_pending"
        return out
    numeric = [t for t in are_totals if isinstance(t, (int, float))]
    out["are_total"] = max(numeric) - min(numeric) if numeric else None

    subscores_per_replicate = [env["subscores"] for env in envelopes]
    if any(s == "_phase1_pending" for s in subscores_per_replicate):
        out["subscores"] = "_phase1_pending"
        return out

    key_sets = [frozenset(s.keys()) for s in subscores_per_replicate]
    if len(set(key_sets)) > 1:
        out["subscores_key_drift"] = sorted(
            set().union(*key_sets) - set().intersection(*key_sets)
        )
        return out
    for sid in sorted(key_sets[0]):
        values = [s[sid] for s in subscores_per_replicate]
        numeric = [v for v in values if isinstance(v, (int, float))]
        if len(numeric) == len(values):
            out[f"subscores.{sid}"] = max(numeric) - min(numeric)
        else:
            out[f"subscores.{sid}"] = values
    return out


def _max_numeric_spread(spread: dict[str, object]) -> float:
    return max(
        (v for v in spread.values() if isinstance(v, (int, float))),
        default=0.0,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "tools" / "calibration-deltas",
    )
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()

    print(
        f"[beaaa5414_track_b_calibration] ARE_PRODUCTION_SCAN_WIRING="
        f"{os.environ.get('ARE_PRODUCTION_SCAN_WIRING')!r}, "
        f"ARE_WALKER_CUSTOMER_AGENT="
        f"{os.environ.get('ARE_WALKER_CUSTOMER_AGENT')!r}",
        file=sys.stderr,
    )

    corpus_path = build_v0_corpus_tarball()
    corpus_snapshot_hash = v0_corpus_snapshot_hash(corpus_path)
    print(
        f"[beaaa5414_track_b_calibration] v0 corpus snapshot hash: "
        f"{corpus_snapshot_hash}",
        file=sys.stderr,
    )

    runtime = make_default_production_scan_runtime()

    envelopes: list[dict[str, object]] = []
    for idx in range(args.n):
        print(
            f"[beaaa5414_track_b_calibration] replicate {idx + 1}/{args.n}",
            file=sys.stderr,
        )
        env = _drive_one_replicate(runtime, idx, corpus_snapshot_hash)
        envelopes.append(env)

    spread = _spread(envelopes)
    canonicals = [_canonical_json(env) for env in envelopes]
    cardinality = len(set(canonicals))
    max_spread = _max_numeric_spread(spread)
    ag_b6_pass = bool(
        max_spread == 0
        and cardinality == 1
        and not any(k.endswith("subscores_key_drift") for k in spread)
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"{today}-beaaa5414-track-b-walker-n5.json"
    report = {
        "ticket": "BEAAA-5414",
        "gate": "AG-B6",
        "agent": "Scanner Engineer",
        "scanner_version": _FIXTURE_SCANNER_VERSION,
        "corpus_version": _FIXTURE_CORPUS_VERSION,
        "probe_set_version": "1.0.0",
        "seed": _FIXTURE_SEED,
        "rubric_point_id": "ALL",
        "are_walker_customer_agent": os.environ.get(
            "ARE_WALKER_CUSTOMER_AGENT"
        ),
        "are_production_scan_wiring": os.environ.get(
            "ARE_PRODUCTION_SCAN_WIRING"
        ),
        "are_scorer_real": os.environ.get("ARE_SCORER_REAL", "on (default)"),
        "n": args.n,
        "corpus_snapshot_hash": corpus_snapshot_hash,
        "envelopes": envelopes,
        "envelope_canonical_jcs": canonicals,
        "envelope_canonical_jcs_cardinality": cardinality,
        "spread": spread,
        "max_numeric_spread": max_spread,
        "ag_b6_pass": ag_b6_pass,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    json_path.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"[beaaa5414_track_b_calibration] JSON: {json_path}",
        file=sys.stderr,
    )

    if ag_b6_pass:
        print(
            f"[beaaa5414_track_b_calibration] AG-B6 PASS: spread=0 across "
            f"N={args.n} replicates, canonical-form cardinality=1.",
            file=sys.stderr,
        )
        return 0

    print(
        f"[beaaa5414_track_b_calibration] AG-B6 FAIL: "
        f"max_numeric_spread={max_spread}, cardinality={cardinality}, "
        f"spread_detail={spread}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
