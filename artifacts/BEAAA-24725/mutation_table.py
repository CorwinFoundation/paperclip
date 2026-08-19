#!/usr/bin/env python3
"""BEAAA-24725 mutation table.

A green suite proves nothing about a control until the control is removed and
the suite goes red. Each mutant below deletes or weakens exactly one of the
Part A / Part B controls in a scratch copy of the tree and runs the named tests;
a mutant that survives means the tests do not actually pin that control.

BEAAA-24534 taught the failure mode this exists to avoid: seven mutants there
all hit the same equality check and none touched the addressing, so a full green
table proved nothing about the property under test. Each mutant here therefore
names the *property* it kills, and the mutants are spread across every control
introduced, not clustered on one line.

Usage: python3 artifacts/BEAAA-24725/mutation_table.py [--json out.json]
Run from the repository root.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = Path("tools/log-tracker")
INGEST = "src/log_tracker/appliance_ingest.py"
SERVER = "src/log_tracker/appliance_ingest_server.py"
NEW_TESTS = "tests/test_appliance_ingest_p1_beaaa24725.py"
OLD_TESTS = "tests/test_appliance_ingest_beaaa20231.py"

# (id, property killed, file, exact old text, replacement, tests that must go red)
MUTANTS = [
    (
        "M1", "the envelope is closed at all", INGEST,
        "    return bool(set(batch) - SEALED_ENVELOPE_FIELDS)",
        "    return False",
        [f"{NEW_TESTS}::test_an_evidence_tier_rider_is_rejected"],
    ),
    (
        "M2", "the allowlist is pinned to the sealed twelve", INGEST,
        'SEALED_ENVELOPE_FIELDS = frozenset(SIGNED_BODY_FIELDS + ("batch_hash", "signature", "key_id"))',
        'SEALED_ENVELOPE_FIELDS = frozenset(SIGNED_BODY_FIELDS + ("batch_hash", "signature", "key_id", "evidence_tier"))',
        [f"{NEW_TESTS}::test_an_evidence_tier_rider_is_rejected",
         f"{NEW_TESTS}::test_a_genuine_sealed_envelope_carries_exactly_the_pinned_keys"],
    ),
    (
        "M3", "an allowlist, not a denylist", INGEST,
        "    return bool(set(batch) - SEALED_ENVELOPE_FIELDS)",
        '    return bool(set(batch) & {"evidence_tier", "tier"})',
        [f"{NEW_TESTS}::test_any_key_outside_the_pinned_set_is_refused"],
    ),
    (
        "M4", "the reason echoes no sender-chosen key", INGEST,
        '            raise IngestRejected("unknown_envelope_field")',
        '            raise IngestRejected(\n                "unknown_envelope_field:"\n                + ",".join(sorted(set(batch) - SEALED_ENVELOPE_FIELDS)))',
        [f"{NEW_TESTS}::test_the_rejection_names_no_sender_chosen_string"],
    ),
    (
        "M5", "the refusal precedes the write transaction", INGEST,
        "        if _unknown_envelope_field(batch):\n"
        '            raise IngestRejected("unknown_envelope_field")\n\n'
        '        events = batch.get("events", [])',
        '        events = batch.get("events", [])',
        [f"{NEW_TESTS}::test_a_refused_envelope_leaves_no_receipt_row",
         f"{NEW_TESTS}::test_an_evidence_tier_rider_is_rejected"],
    ),
    (
        "M6", "the check follows signature verification", INGEST,
        "        # Sealed contract verifier recomputes the canonical batch hash and checks\n"
        "        # the Ed25519 signature. This must happen before the duplicate lookup.\n"
        "        if not self.verify_batch(batch, public_key):",
        "        if _unknown_envelope_field(batch):\n"
        '            raise IngestRejected("unknown_envelope_field")\n'
        "        if not self.verify_batch(batch, public_key):",
        [f"{NEW_TESTS}::test_the_envelope_check_runs_after_signature_verification"],
    ),
    (
        "M7", "the BEAAA-24478 forgery still reports the leak", INGEST,
        "        leaked_path = _find_prohibited(",
        "        if _unknown_envelope_field(batch):\n"
        '            raise IngestRejected("unknown_envelope_field")\n'
        "        leaked_path = _find_prohibited(",
        [f"{OLD_TESTS}::test_a_dotted_top_level_key_cannot_forge_the_histogram_exemption",
         f"{NEW_TESTS}::test_a_rider_whose_subtree_leaks_content_reports_the_leak"],
    ),
    (
        "M8", "a log line carries the class, not the rendered path", INGEST,
        '    return reason.split(":", 1)[0]',
        "    return reason",
        [f"{NEW_TESTS}::test_the_line_carries_the_reason_class_never_the_rendered_path",
         f"{NEW_TESTS}::test_a_content_leak_is_logged_as_a_class_and_never_as_a_path"],
    ),
    (
        "M9", "a sender-named appliance_id cannot become a log channel", INGEST,
        "    return value if isinstance(value, str) and _SAFE_APPLIANCE_ID.match(value) else None",
        "    return value if isinstance(value, str) else None",
        [f"{NEW_TESTS}::test_a_sender_named_appliance_id_cannot_become_a_log_channel"],
    ),
    (
        "M10", "counters are per-tenant", INGEST,
        "            tally = self._tenants.setdefault(tenant_id_hash, self._empty())",
        '            tally = self._tenants.setdefault("*", self._empty())',
        [f"{NEW_TESTS}::test_metrics_counters_do_not_cross_tenants",
         f"{NEW_TESTS}::test_a_silent_tenant_and_a_wholly_rejected_tenant_no_longer_look_identical"],
    ),
    (
        "M11", "the chain tip is scoped to the reading tenant", INGEST,
        '                "WHERE tenant_id_hash=? GROUP BY appliance_id",\n                (tenant_id_hash,),',
        '                "GROUP BY appliance_id",\n                (),',
        [f"{NEW_TESTS}::test_the_chain_tip_is_per_appliance_and_survives_a_restart",
         f"{NEW_TESTS}::test_a_tenant_with_no_receipts_reads_an_empty_chain_tip"],
    ),
    (
        "M12", "a snapshot cannot be mutated through", INGEST,
        '            return {**tally, "rejected_total": dict(tally["rejected_total"])}',
        "            return tally",
        [f"{NEW_TESTS}::test_a_snapshot_cannot_be_mutated_through"],
    ),
    (
        "M13", "counter updates are atomic", INGEST,
        "        with self._lock:\n            tally = self._tenants.setdefault(tenant_id_hash, self._empty())",
        "        if True:\n            tally = self._tenants.setdefault(tenant_id_hash, self._empty())\n"
        "            import time as _t; _t.sleep(0)",
        [f"{NEW_TESTS}::test_counters_are_atomic_under_concurrent_requests"],
    ),
    (
        "M14", "every request outcome is logged, including a failed send", SERVER,
        "            finally:\n"
        "                # In ``finally`` so an outcome is always recorded, including when\n"
        "                # writing the response itself fails on a dropped connection.\n"
        "                store.record_outcome(tenant_id=tenant_id, outcome=outcome, reason=reason)",
        "            finally:\n"
        "                store.record_outcome(tenant_id=tenant_id, outcome=outcome, reason=reason)\n"
        "                return",
        [f"{NEW_TESTS}::test_each_request_outcome_writes_exactly_one_line"],
    ),
    (
        "M15", "a malformed body is deterministic, not a retryable outage", SERVER,
        "            if not isinstance(payload, dict):",
        "            if False:",
        [f"{NEW_TESTS}::test_a_non_object_body_is_a_deterministic_422_not_a_retryable_503"],
    ),
    (
        "M16", "a decoder message is not a reason class", SERVER,
        '                reason = str(exc) if isinstance(exc, IngestRejected) else "malformed_body"',
        "                reason = str(exc)",
        [f"{NEW_TESTS}::test_undecodable_json_is_classified_without_echoing_the_decoder"],
    ),
    (
        "M17", "/metrics is still tenant-scoped after the surface grew", INGEST,
        "        if not any(hmac.compare_digest(row[0], expected) for row in rows):\n"
        '            raise IngestRejected("unauthorized_scope")\n'
        "        return {",
        "        return {",
        [f"{NEW_TESTS}::test_the_extended_metrics_keep_the_f5_scoping",
         f"{NEW_TESTS}::test_metrics_is_still_refused_across_tenants_and_without_a_token",
         f"{OLD_TESTS}::test_scoped_status_requires_the_enrolled_token_and_never_crosses_tenants"],
    ),
    (
        "M18", "an unnamed outcome is refused rather than logged", INGEST,
        '    if outcome not in INGEST_OUTCOMES:\n        raise ValueError("unknown ingest outcome")\n    moment =',
        "    moment =",
        [f"{NEW_TESTS}::test_an_unnamed_outcome_is_a_programming_error_not_a_log_line"],
    ),
]


def pytest(tests: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header",
         "-p", "no:cacheprovider", *tests],
        cwd=cwd, capture_output=True, text=True,
    )


def run(root: Path, mutant) -> dict:
    """Patch in place and restore.

    The tests resolve the sealed appliance relative to the repository
    (``parents[3]``), so a scratch *copy* of the package cannot import the
    contract and every mutant would appear killed by an import error rather than
    by the property under test. Patching the real file and restoring it in a
    ``finally`` is the only variant that kills for the right reason.
    """
    ident, prop, target, old, new, tests = mutant
    package = root / PACKAGE
    path = package / target
    original = path.read_text(encoding="utf-8")
    if original.count(old) != 1:
        return {"id": ident, "property": prop, "file": target, "tests": tests,
                "status": "NOT_APPLIED",
                "detail": f"anchor matched {original.count(old)} times, expected 1"}
    try:
        path.write_text(original.replace(old, new, 1), encoding="utf-8")
        proc = pytest(tests, package)
    finally:
        path.write_text(original, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == original, "restore failed"
    return {"id": ident, "property": prop, "file": target, "tests": tests,
            "status": "KILLED" if proc.returncode != 0 else "SURVIVED",
            "returncode": proc.returncode,
            "detail": (proc.stdout.strip().splitlines() or [""])[-1]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json")
    args = parser.parse_args()
    root = Path.cwd()
    if not (root / PACKAGE).is_dir():
        print("run from the repository root", file=sys.stderr)
        return 2

    # A kill means nothing unless the same tests pass unmutated.
    every_test = sorted({test for mutant in MUTANTS for test in mutant[5]})
    baseline = pytest(every_test, root / PACKAGE)
    if baseline.returncode != 0:
        print("baseline is not green; a kill would prove nothing", file=sys.stderr)
        print(baseline.stdout[-2000:], file=sys.stderr)
        return 2
    print(f"baseline: {len(every_test)} named tests green\n")

    results = [run(root, mutant) for mutant in MUTANTS]
    width = max(len(r["property"]) for r in results)
    for result in results:
        print(f"{result['id']:>4}  {result['status']:<11} {result['property']:<{width}}  "
              f"{result['detail']}")
    survivors = [r for r in results if r["status"] != "KILLED"]
    print(f"\n{len(results) - len(survivors)}/{len(results)} killed")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
