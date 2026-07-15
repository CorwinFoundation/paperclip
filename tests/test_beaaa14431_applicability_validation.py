"""BEAAA-14431 (F11 / F19) — validate caller-supplied applicability_decisions
against the authoritative policy engine; kill the N/A inflation path.

F11 (BEAAA-14413): the CLI `score()` path honored a scanned-agent-authored
`applicability.json` sidecar (under the `scanner_invocation_context.json`
legacy-replay flag) and dropped any dim marked `fires:false` from the pillar
mean with NO validation. An agent could mark its worst dims N/A and inflate
`are_total` (~8-9 pts), traced only by a `coverage_caveat`.

Fix: run_scan validates every caller-asserted N/A against
`harness.applicability.decide(policy, architecture_flags)` and fails closed
(CliInputError, exit 2) on anything the engine does not independently confirm.

F19 counterpart: when a sidecar is present but NOT honored (legacy flag off),
an additive `applicability_sidecar_ignored` diagnostic surfaces the discard.

TEST-FIRST: these tests are RED on canon (origin/master) — there run_scan
honors the malicious sidecar and inflates instead of raising — and GREEN with
the fix. See the module docstring's inflation demonstration in
`test_out_of_policy_na_waiver_fails_closed`.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.applicability import applicability_record, load_policy  # noqa: E402
from harness.cli_runner import CliInputError, run_scan  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
G1 = REPO / "tests" / "fixtures" / "golden_agents" / "G1"
G1_EVAL = G1 / "eval_outputs.json"
G1_APPLICABILITY = G1 / "applicability.json"
FIXED_SCORED_AT = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)

# Honest architecture flags for the G1 surface. All false except a system
# prompt echoable in the response channel — a consumer-chat deployment.
G1_FLAGS = json.loads(G1_APPLICABILITY.read_text())["architecture_flags"]

# The N/A set the policy engine independently sanctions for G1_FLAGS:
#   {P2.2 (no UW labels), P3.2 (no external content channel),
#    ROBUSTNESS.TOOL_MISUSE.* (no tool surface)}.
# PII dims are fires_when:always → the engine rules them FIRE; a sidecar may
# NOT waive them. This is the compliant baseline used across the tests.
_POLICY_COMPLIANT = applicability_record(load_policy(), G1_FLAGS)

_LEGACY_REPLAY_CTX = {
    "purpose": "legacy_replay",
    "applicability_mode": "legacy_replay",
    "non_binding_compatibility": True,
}


def _write_inputs(app_payload: dict, invocation: dict) -> Path:
    d = Path(tempfile.mkdtemp()) / "G1"
    d.mkdir(parents=True)
    (d / "eval_results.json").write_text(G1_EVAL.read_text())
    (d / "applicability.json").write_text(json.dumps(app_payload))
    (d / "scanner_invocation_context.json").write_text(json.dumps(invocation))
    return d


def _run(app_payload: dict, invocation: dict) -> dict:
    return run_scan(
        agent_id="G1",
        input_dir=_write_inputs(app_payload, invocation),
        seed=42,
        corpus_version="0.1.1",
        rubric_path=RUBRIC_PATH,
        scored_at=FIXED_SCORED_AT,
    )


class ApplicabilityValidationTests(unittest.TestCase):
    # ---- (a) fail-closed on the inflation path --------------------------
    def test_out_of_policy_na_waiver_fails_closed(self):
        """AC(a): a sidecar marking the worst FIRING dims N/A inflates on the
        unvalidated score path but fails closed through run_scan after the fix."""
        rub = Rubric.load(RUBRIC_PATH)
        active = set(rub.active_dim_ids)
        ev = json.loads(G1_EVAL.read_text())
        na_now = set(_POLICY_COMPLIANT["na_dims"])
        worst = [
            d for d, _ in sorted(
                ((d, ev[d]["raw"]) for d in ev if d in active),
                key=lambda kv: kv[1],
            ) if d not in na_now
        ][:4]

        malicious = copy.deepcopy(_POLICY_COMPLIANT)
        for did in worst:
            malicious["applicability_decisions"][did] = {
                "fires": False,
                "na_justification": "agent asserts this worst dim is N/A",
                "na_justification_key": None,
            }
            malicious.setdefault("na_dims", []).append(did)

        # The vulnerability is real: on the UNVALIDATED engine path the
        # suppression inflates are_total, traced only by a coverage_caveat.
        kw = dict(
            snapshot_hash="sha256:x", forensic_bundle_uri="u",
            scored_at=FIXED_SCORED_AT, corpus_version="0.1.1", seed=42,
        )
        base = score(rub, ev, applicability_decisions=_POLICY_COMPLIANT["applicability_decisions"], **kw)
        inflated = score(rub, ev, applicability_decisions=malicious["applicability_decisions"], **kw)
        self.assertGreater(
            inflated["are_total"], base["are_total"],
            "expected the malicious sidecar to inflate on the unvalidated path",
        )
        self.assertTrue(
            any(d["code"] == "coverage_caveat" for d in inflated.get("diagnostics", [])),
        )

        # After the fix: run_scan validates against the policy engine and
        # fails closed. RED on canon (canon returns the inflated envelope).
        with self.assertRaises(CliInputError) as ctx:
            _run(malicious, _LEGACY_REPLAY_CTX)
        self.assertIn("rejected", str(ctx.exception))

    # ---- (b) policy-compliant N/A still accepted, byte-identical --------
    def test_policy_compliant_na_still_accepted(self):
        """AC(b): decisions the engine independently rules N/A for the same
        flags are still honored — validation is a no-op, output byte-identical
        to the direct (unvalidated) score() call with the same decisions."""
        result = _run(_POLICY_COMPLIANT, _LEGACY_REPLAY_CTX)

        rub = Rubric.load(RUBRIC_PATH)
        ev = json.loads(G1_EVAL.read_text())
        direct = score(
            rub, ev,
            snapshot_hash=result["snapshot_hash"],
            forensic_bundle_uri=result["forensic_bundle_uri"],
            scored_at=FIXED_SCORED_AT,
            scanner_version=result["scanner_version"],
            corpus_version="0.1.1",
            seed=42,
            applicability_decisions=_POLICY_COMPLIANT["applicability_decisions"],
            applicability_policy_version=_POLICY_COMPLIANT.get("policy_version"),
        )
        self.assertEqual(result["are_total"], direct["are_total"])
        for pid in ("OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE"):
            self.assertAlmostEqual(
                result["subscores"][pid], direct["pillars"][pid]["score"], places=6,
            )
        # No fail-closed rejection, and PII (fires_when:always) is scored, not waived.
        self.assertNotIn("ROBUSTNESS.PII.SCAN_TIME_DETECTION", result["uncovered_dimensions"])
        self.assertNotIn("GOVERNANCE.PII.HANDLING_ATTESTATION", result["uncovered_dimensions"])

    # ---- (c) F19: sidecar present but ignored → diagnostic -------------
    def test_ignored_sidecar_emits_diagnostic(self):
        """AC(c) / F19: when the sidecar is NOT honored (non-legacy invocation
        context), the discard is surfaced via an `applicability_sidecar_ignored`
        diagnostic carrying the dropped-decision count — no silent discard."""
        result = _run(_POLICY_COMPLIANT, {"purpose": "pre_bind"})
        diags = {d["code"]: d for d in result.get("diagnostics", [])}
        self.assertIn("applicability_sidecar_ignored", diags)
        dropped = [
            did for did, dec in _POLICY_COMPLIANT["applicability_decisions"].items()
            if dec.get("fires") is False
        ]
        self.assertEqual(
            diags["applicability_sidecar_ignored"]["detail"]["dropped_decision_count"],
            len(dropped),
        )
        self.assertEqual(
            sorted(diags["applicability_sidecar_ignored"]["detail"]["dropped_dims"]),
            sorted(dropped),
        )
        # Ignored path scores every dim — no dim is dropped from the mean.
        self.assertEqual(result["uncovered_dimensions"], [])

    def test_honored_compliant_sidecar_has_no_ignored_diagnostic(self):
        """The F19 diagnostic must NOT fire when the sidecar is actually honored."""
        result = _run(_POLICY_COMPLIANT, _LEGACY_REPLAY_CTX)
        codes = {d["code"] for d in result.get("diagnostics", [])}
        self.assertNotIn("applicability_sidecar_ignored", codes)

    # ---- fail-closed hardening -----------------------------------------
    def test_missing_architecture_flags_fails_closed(self):
        """A sidecar asserting N/A without architecture_flags cannot be
        independently confirmed → fail-closed."""
        payload = copy.deepcopy(_POLICY_COMPLIANT)
        payload.pop("architecture_flags", None)
        with self.assertRaises(CliInputError):
            _run(payload, _LEGACY_REPLAY_CTX)

    def test_unknown_dim_waiver_fails_closed(self):
        """A well-formed but non-active dim asserted N/A → fail-closed."""
        payload = copy.deepcopy(_POLICY_COMPLIANT)
        payload["applicability_decisions"]["GOVERNANCE.FAKE.DIM"] = {
            "fires": False, "na_justification": "x", "na_justification_key": None,
        }
        with self.assertRaises(CliInputError):
            _run(payload, _LEGACY_REPLAY_CTX)

    def test_tampered_flags_rejected_by_engine(self):
        """Unknown architecture_flags (adapter-drift) are rejected by the
        policy engine → fail-closed rather than silently trusting the waiver."""
        payload = copy.deepcopy(_POLICY_COMPLIANT)
        payload["architecture_flags"] = dict(payload["architecture_flags"])
        payload["architecture_flags"]["has_made_up_flag"] = True
        with self.assertRaises(CliInputError):
            _run(payload, _LEGACY_REPLAY_CTX)


if __name__ == "__main__":
    unittest.main()
