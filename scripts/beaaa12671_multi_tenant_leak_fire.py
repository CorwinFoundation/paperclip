"""BEAAA-12671 — probe-fires proof for P3.3 multi_tenant_leak.

Realizes the dormant P3.3 ``multi_tenant_leak`` sub-probe against fixture
multi-tenant customer-service agents and writes the evidence artifacts the
ticket requires:

  * artifacts/beaaa-12671/multi_tenant_leak_FIRE_leaky.json      (fires; LEAK_DETECTED)
  * artifacts/beaaa-12671/multi_tenant_leak_FIRE_resistant.json  (fires; RESISTED)
  * artifacts/beaaa-12671/multi_tenant_leak_NA_single_tenant.json (N/A; flag false)

Coverage-realization only — no scoring math, no rubric/applicability_policy.yaml
change. The verdicts are probe-fires evidence, not scored contributions to
are_total (see harness/subprobe_applicability.py module docstring).

Usage:
    python3 scripts/beaaa12671_multi_tenant_leak_fire.py [--out-dir PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness import subprobe_applicability as sp  # noqa: E402
from harness.runner import MockRunner  # noqa: E402

FIXTURE_DIR = REPO / "fixtures" / "multi_tenant"
DEFAULT_OUT = REPO / "artifacts" / "beaaa-12671"
DEFAULT_REFUSAL = "I can't share other users' conversations."


def _fixture_runner(fixture: dict, family: dict) -> MockRunner:
    """Build a deterministic runner from a fixture's per-seed-id responses.

    The seed payloads come from the probe-family YAML (source of truth); the
    fixture supplies the response keyed by seed id, so the two never drift.
    """
    seeds = {s.id: s.payload for s in sp._seeds_for(family, sp.MULTI_TENANT_CATEGORY)}
    by_seed = fixture.get("responses_by_seed_id", {})
    payload_to_response = {
        seeds[sid]: resp for sid, resp in by_seed.items() if sid in seeds
    }
    return MockRunner(
        fixture["surface_id"],
        lambda prompt: payload_to_response.get(prompt, DEFAULT_REFUSAL),
    )


def _run_fixture(fixture_path: Path, family: dict) -> dict:
    fixture = json.loads(fixture_path.read_text())
    runner = _fixture_runner(fixture, family)
    result = sp.run_multi_tenant_leak(
        runner,
        sibling_tenant_marker=fixture["sibling_tenant_marker"],
        flags=fixture["architecture_flags"],
        family=family,
    )
    return {
        "ticket": "BEAAA-12671",
        "surface_id": fixture["surface_id"],
        "archetype": fixture["archetype"],
        "fixture": str(fixture_path.relative_to(REPO)),
        "multi_tenant_basis": fixture.get("multi_tenant_basis"),
        "probe_result": result,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="BEAAA-12671 multi_tenant_leak probe-fires proof.")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    family = sp.load_family()
    plan = [
        ("customer_service_multitenant_leaky.json", "multi_tenant_leak_FIRE_leaky.json"),
        ("customer_service_multitenant_resistant.json", "multi_tenant_leak_FIRE_resistant.json"),
        ("customer_service_single_tenant.json", "multi_tenant_leak_NA_single_tenant.json"),
    ]
    written = []
    for fixture_name, out_name in plan:
        record = _run_fixture(FIXTURE_DIR / fixture_name, family)
        out_path = out_dir / out_name
        out_path.write_text(json.dumps(record, indent=2) + "\n")
        written.append(out_path)
        r = record["probe_result"]
        status = r["status"]
        verdict = r.get("verdict") or r.get("na_justification_key")
        print(f"  {record['surface_id']}: fired={r['fired']} status={status} -> {verdict}",
              file=sys.stderr)

    print(json.dumps({"written": [str(p.relative_to(REPO)) for p in written]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
