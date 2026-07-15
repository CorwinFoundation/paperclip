"""BEAAA-10945 — live-edge provenance enablement (producer side + custody).

Reconciled onto master under BEAAA-12659. The original suite (feature branch
``beaaa-10945-live-edge-provenance``) drove the read-path through the interim
``api.auth.PrincipalResolver`` (BEAAA-10906). Master superseded that read-path
auth with the ``EdgeGateway`` key store (BEAAA-11867) + ``_authenticated_tenant_id``
/ uw-entitlement edge (BEAAA-11858), so the provenance suite is re-expressed
against master's ``/score`` edge (``X-Tenant-Id`` tenant context). The
provenance *invariants* proven are unchanged:

* the hosted eval runner signs each emitted ``eval_outputs`` dim entry with the
  shared ``ARE_EVAL_PROVENANCE_KEY`` via the canonical
  :class:`api.intake_guard.ProvenanceSigner` — and a payload signed that way is
  accepted by an edge wired with the *same* key (signer↔verifier lockstep);
* the produced score is byte-for-byte identical to the pre-signing score
  (``provenance_sig`` never reaches the scoring engine — ``handlers`` projects
  only ``raw``/``evidence_uri`` into ``engine_inputs``), so N=5 golden delta is
  0 by construction;
* the BEAAA-10913 dynamic exploit shape — a client-minted ``runner`` + UUID
  ``eval_run_id`` with a forged/absent ``provenance_sig`` — is rejected 422 on
  the keyed edge (SEC-6 fail-closed);
* a pilot/prod runtime refuses to start without ``ARE_EVAL_PROVENANCE_KEY``
  (same custody class as ``ARE_BUNDLE_SIGNING_KEY``), so the live edge cannot
  silently run with the verifier inert.

Classification: rule-execution under scanner-implementation-supervision-v1
(operational secret wiring + existing verifier path). No rubric / scoring math /
band-cut / output-shape change.
"""
from __future__ import annotations

import copy
import os
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except Exception as exc:  # pragma: no cover
    raise unittest.SkipTest(f"fastapi TestClient unavailable: {exc}")

from api.app import create_app
from api.bundle import BundleStore
from api.intake_guard import (
    HOSTED_RUNNER,
    EvalProvenanceVerifier,
    IntakeError,
    ProvenanceSigner,
)
from api.runtime_guards import RuntimeGuardError, validate_runtime_environment
from rubric.scoring import Rubric

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

KEY = b"live-edge-provenance-key-10945"
PROVENANCE_ENV = "ARE_EVAL_PROVENANCE_KEY"

TENANT_A = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
SNAPSHOT_HASH = "sha256:" + "a" * 64


def _tenant(tenant_id: str = TENANT_A) -> dict:
    # Master's /score edge derives tenant context from X-Tenant-Id in the
    # local/test profile (no gateway, no token map). See api/app._tenant_context.
    return {"X-Tenant-Id": tenant_id}


def _score_request(rubric: Rubric, raw: float = 2.0) -> dict:
    """A hosted-eval /score body BEFORE provenance signing.

    Mirrors what the hosted eval runner assembles: every dim carries
    ``runner=beaai-hosted-eval`` + a minted UUID ``eval_run_id`` but NO
    ``provenance_sig`` yet — the signing step is applied by the runner.
    """
    return {
        "tenant_id": TENANT_A,
        "corpus_version": "0.1.0",  # Supreme-Law pin; a real hosted-run body carries it
        "snapshot": {
            "model_id": "acme-llm",
            "model_version": "v1.2.3",
            "prompt_version": "p7",
            "snapshot_hash": SNAPSHOT_HASH,
        },
        "eval_outputs": {
            did: {
                "raw": raw,
                "evidence_uri": f"https://evidence.local/{did}",
                "runner": HOSTED_RUNNER,
                "eval_run_id": RUN_ID,
            }
            for did in rubric.active_dim_ids
        },
    }


class ProducerSignerLockstepTests(unittest.TestCase):
    """The runner signs; an edge wired with the same key accepts and scores."""

    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)

    def _keyed_edge(self) -> TestClient:
        app = create_app(
            rubric=self.rubric,
            bundles=BundleStore(),
            provenance_verifier=EvalProvenanceVerifier(KEY),
        )
        return TestClient(app)

    def _unkeyed_edge(self) -> TestClient:
        # Offline / dev posture: no verifier wired (legacy string+UUID check).
        app = create_app(rubric=self.rubric, bundles=BundleStore())
        return TestClient(app)

    def test_producer_signed_payload_accepted_and_score_unchanged(self):
        # Baseline: legacy edge (verifier inert), unsigned hosted-run body.
        baseline = self._unkeyed_edge().post(
            "/score", json=_score_request(self.rubric), headers=_tenant()
        )
        self.assertEqual(baseline.status_code, 200, baseline.text)

        # Live edge: verifier wired. The runner signs with the SAME key.
        signed = _score_request(self.rubric)
        ProvenanceSigner(KEY).stamp_eval_outputs(
            signed["eval_outputs"],
            tenant_id=signed["tenant_id"],
            snapshot_hash=signed["snapshot"]["snapshot_hash"],
        )
        for entry in signed["eval_outputs"].values():
            self.assertIn("provenance_sig", entry)
        r = self._keyed_edge().post("/score", json=signed, headers=_tenant())
        self.assertEqual(r.status_code, 200, r.text)

        # Signing changes NO scoring behavior: provenance_sig never reaches the
        # engine (handlers project only raw/evidence_uri). are_total + band are
        # the deterministic scoring outputs; scored_at / bundle uri are volatile.
        b, s = baseline.json(), r.json()
        self.assertEqual(s["are_total"], b["are_total"])
        self.assertEqual(s["band"], b["band"])

    def test_from_env_signer_and_verifier_share_the_key(self):
        # Deploy-time lockstep: one env var feeds BOTH the runner's signer and
        # the edge's verifier. Prove a from_env-signed payload is from_env-verified.
        saved = os.environ.get(PROVENANCE_ENV)
        os.environ[PROVENANCE_ENV] = KEY.decode()
        try:
            signer = ProvenanceSigner.from_env()
            verifier = EvalProvenanceVerifier.from_env()
            self.assertIsNotNone(signer)
            self.assertIsNotNone(verifier)
            req = _score_request(self.rubric)
            signer.stamp_eval_outputs(
                req["eval_outputs"],
                tenant_id=req["tenant_id"],
                snapshot_hash=req["snapshot"]["snapshot_hash"],
            )
            for dim_id, entry in req["eval_outputs"].items():
                # verifier accepts (no raise) exactly what the signer produced
                verifier.verify(
                    tenant_id=req["tenant_id"],
                    snapshot_hash=req["snapshot"]["snapshot_hash"],
                    dim_id=dim_id,
                    entry=entry,
                )
        finally:
            if saved is None:
                os.environ.pop(PROVENANCE_ENV, None)
            else:
                os.environ[PROVENANCE_ENV] = saved


class ForgedProvenanceRejectedTests(unittest.TestCase):
    """BEAAA-10913 exploit shape re-run against the keyed live edge → 422."""

    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.client = TestClient(
            create_app(
                rubric=self.rubric,
                bundles=BundleStore(),
                provenance_verifier=EvalProvenanceVerifier(KEY),
            )
        )

    def test_absent_sig_hosted_run_rejected_422(self):
        r = self.client.post(
            "/score", json=_score_request(self.rubric), headers=_tenant()
        )
        self.assertEqual(r.status_code, 422, r.text)

    def test_forged_sig_hosted_run_rejected_422(self):
        req = _score_request(self.rubric)
        for entry in req["eval_outputs"].values():
            entry["provenance_sig"] = "0" * 64  # well-formed but not server-issued
        r = self.client.post("/score", json=req, headers=_tenant())
        self.assertEqual(r.status_code, 422, r.text)

    def test_sig_from_wrong_key_rejected_422(self):
        # A runner signing with a DIFFERENT key (e.g. stale/rotated) is not
        # trusted by the edge — protects against key drift.
        req = _score_request(self.rubric)
        ProvenanceSigner(b"a-different-key").stamp_eval_outputs(
            req["eval_outputs"],
            tenant_id=req["tenant_id"],
            snapshot_hash=req["snapshot"]["snapshot_hash"],
        )
        r = self.client.post("/score", json=req, headers=_tenant())
        self.assertEqual(r.status_code, 422, r.text)


class ProvenanceSignerUnitTests(unittest.TestCase):
    def test_from_env_none_without_key(self):
        saved = os.environ.pop(PROVENANCE_ENV, None)
        try:
            self.assertIsNone(ProvenanceSigner.from_env())
        finally:
            if saved is not None:
                os.environ[PROVENANCE_ENV] = saved

    def test_stamp_raises_on_missing_eval_run_id(self):
        signer = ProvenanceSigner(KEY)
        bad = {"P1.1": {"raw": 2.0, "runner": HOSTED_RUNNER}}
        with self.assertRaises(IntakeError):
            signer.stamp_eval_outputs(
                bad,
                tenant_id=TENANT_A,
                snapshot_hash=SNAPSHOT_HASH,
            )

    def test_stamp_skips_non_hosted_entries(self):
        signer = ProvenanceSigner(KEY)
        eo = {"P1.1": {"raw": 2.0, "runner": "self-report", "eval_run_id": RUN_ID}}
        before = copy.deepcopy(eo)
        signer.stamp_eval_outputs(
            eo,
            tenant_id=TENANT_A,
            snapshot_hash=SNAPSHOT_HASH,
        )
        # non-hosted entry left untouched (edge rejects it anyway on runner)
        self.assertEqual(eo, before)

    def test_empty_mapping_raises(self):
        with self.assertRaises(IntakeError):
            ProvenanceSigner(KEY).stamp_eval_outputs(
                {},
                tenant_id=TENANT_A,
                snapshot_hash=SNAPSHOT_HASH,
            )


class ProvenanceCustodyGuardTests(unittest.TestCase):
    """Pilot/prod must not start without the provenance key (same custody
    class as ARE_BUNDLE_SIGNING_KEY)."""

    def _pilot_env(self, with_key: bool) -> dict:
        env = {
            "ARE_RUNTIME_PROFILE": "pilot",
            "ARE_BUNDLE_SIGNING_KEY": "bundle-key",
        }
        if with_key:
            env["ARE_EVAL_PROVENANCE_KEY"] = KEY.decode()
        return env

    def test_pilot_missing_provenance_key_refuses_start(self):
        # The provenance-key guard precedes the in-memory-bundle guard, so an
        # in-memory BundleStore still surfaces the provenance failure first.
        with self.assertRaises(RuntimeGuardError) as ctx:
            validate_runtime_environment(
                bundles=BundleStore(),
                scan_runtime=None,
                env=self._pilot_env(with_key=False),
            )
        self.assertIn("ARE_EVAL_PROVENANCE_KEY", str(ctx.exception))

    def test_pilot_with_provenance_key_passes_the_provenance_check(self):
        # With the key present the provenance guard no longer trips. A later
        # guard (in-memory bundle store) may still raise; assert only that the
        # failure is NOT the provenance-key one.
        try:
            validate_runtime_environment(
                bundles=BundleStore(),
                scan_runtime=None,
                env=self._pilot_env(with_key=True),
            )
        except RuntimeGuardError as exc:
            self.assertNotIn("ARE_EVAL_PROVENANCE_KEY", str(exc))

    def test_local_dev_does_not_require_provenance_key(self):
        # Offline / dev posture: no strict-profile requirement.
        validate_runtime_environment(
            bundles=BundleStore(),
            scan_runtime=None,
            env={"ARE_RUNTIME_PROFILE": "local_dev"},
        )


if __name__ == "__main__":
    unittest.main()
