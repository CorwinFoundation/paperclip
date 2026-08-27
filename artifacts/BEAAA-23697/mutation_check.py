#!/usr/bin/env python3
"""Mutation check for the BEAAA-23697 continuity acceptance suite.

Answers the question BEAAA-23829/F4 raised: does the suite actually detect a
regression in the code it covers, or does it pass regardless? Each mutant below
is a plausible, silent break. Every one must make the suite fail.

    python3 artifacts/BEAAA-23697/mutation_check.py

Exit 0 means every mutant was killed. Exit 1 means at least one survived and the
suite is not load-bearing for that behaviour.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = Path("tools/post-bind-ingest")
RUNTIME = PKG / "src/post_bind_ingest/runtime_continuity.py"
CLI = PKG / "src/post_bind_ingest/cli.py"
SUITE = "tests/test_runtime_continuity_beaaa23697.py"

# (id, description, file, exact text to replace, replacement)
MUTANTS = [
    (
        "M1", "rollback flag defaults to on", RUNTIME,
        "def __init__(self, path: str | Path, *, enabled: bool = False) -> None:",
        "def __init__(self, path: str | Path, *, enabled: bool = True) -> None:",
    ),
    (
        "M2", "ingest kill switch deleted", RUNTIME,
        '        if not self.enabled:\n'
        '            self._audit(stream_key, "ingest_refused", "continuity_runtime_disabled", None, evidence_ref, payload)\n'
        '            raise RuntimeContinuityError("continuity_runtime_disabled")\n',
        "",
    ),
    (
        "M3", "signature failure swallowed", RUNTIME,
        "        normalized = normalize_receiver_events(dict(payload), jwt_keyring=jwt_keyring)",
        "        try:\n"
        "            normalized = normalize_receiver_events(dict(payload), jwt_keyring=jwt_keyring)\n"
        "        except Exception:\n"
        "            normalized = []",
    ),
    (
        "M4", "persisted gap state never written back", RUNTIME,
        '                self.db.execute("UPDATE streams SET gap_active=? WHERE stream_key=?", (int(decision.gap_state.active), stream_key))',
        "                pass",
    ),
    (
        "M6", "Scanner contract reference corrupted", RUNTIME,
        'SCANNER_CONTRACT_REF = "are-scanner-api-v1@v1.3.0"',
        'SCANNER_CONTRACT_REF = "are-scanner-api-v1@v1.2.0"',
    ),
    (
        "M7", "sequence violation not persisted", RUNTIME,
        "            self._record_violation(stream_key, reason, expected, batch_seq,\n"
        "                                   tenant_id=tenant_id, agent_id=agent_id,\n"
        "                                   deployment_id=deployment_id, collector_id=collector_id)",
        "            pass",
    ),
    (
        "M8", "gate events dropped before the spine", RUNTIME,
        "        for event in decision.audit_events:\n"
        "            self._audit_gate_event(stream_key, event, action_class, evidence_ref)",
        "        for event in ():\n"
        "            self._audit_gate_event(stream_key, event, action_class, evidence_ref)",
    ),
    (
        "M9", "CLI arms the runtime regardless of the flag", CLI,
        "    runtime = PersistentContinuityRuntime(args.continuity_db, enabled=args.enable_continuity_runtime)\n"
        "    try:\n"
        "        result = runtime.ingest_signed_batch(",
        "    runtime = PersistentContinuityRuntime(args.continuity_db, enabled=True)\n"
        "    try:\n"
        "        result = runtime.ingest_signed_batch(",
    ),
]

def run_suite(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", SUITE, "-q", "-x"],
        cwd=tree / PKG, capture_output=True, text=True, timeout=1200,
    )


def main() -> int:
    baseline = run_suite(REPO)
    if baseline.returncode != 0:
        print("BASELINE SUITE IS NOT GREEN — mutation results would be meaningless")
        print(baseline.stdout[-2000:])
        return 1
    print(f"baseline: PASS  ({baseline.stdout.strip().splitlines()[-1]})")

    failures = []
    for mutant_id, description, target, old, new in MUTANTS:
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            shutil.copytree(REPO / PKG, tree / PKG, symlinks=True)
            path = tree / target
            source = path.read_text(encoding="utf-8")
            if old not in source:
                print(f"{mutant_id}: SKIP — anchor text not found in {target}")
                failures.append(mutant_id)
                continue
            path.write_text(source.replace(old, new, 1), encoding="utf-8")
            result = run_suite(tree)
            killed = result.returncode != 0
            print(f"{mutant_id}: {'KILLED ' if killed else 'SURVIVED'} — {description}")
            if not killed:
                failures.append(mutant_id)

    print()
    if failures:
        print(f"RESULT: {len(failures)} mutant(s) survived: {', '.join(failures)}")
        return 1
    print(f"RESULT: all {len(MUTANTS)} mutants killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
