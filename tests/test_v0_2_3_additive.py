"""v0.2.3 additive tests (BEAAA-21, UW co-signed amendments A + B + C).

Covers:
  - Amendment A: ScoreResponse always carries scanner_version (required).
  - Amendment B: corpus_version + seed are server-stamped on the response;
    seed defaults to 42 when request omits it; corpus_version mismatch
    surfaces in diagnostics; request fields are caller-asserted hints.
  - Amendment C: snapshot_hash canonicalization — single-model preimage
    bytes-for-bytes match the v0.2.2 reference recipe; multi-model preimage
    includes model_versions; two multi-model snapshots differing only in
    role→version produce distinct snapshot_hash values.
  - Cross-version compat: v0.2.2-shaped requests (no v0.2.3 fields) produce
    valid v0.2.3 responses with default-stamped server values.
"""
from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from api.bundle import BundleStore
from api.handlers import score_handler
from api.intake_guard import HOSTED_RUNNER
from rubric.scoring import Rubric, score
from scanners import DEFAULT_SEED, SCANNER_VERSION
from scanners.snapshot_hash import (
    canonical_preimage_bytes,
    canonicalize_snapshot_hash,
)

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

TENANT = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
SNAPSHOT_HASH = "sha256:" + "a" * 64
SCORED_AT = datetime(2026, 5, 9, tzinfo=timezone.utc)


def _v022_canonical_bytes(*, provider, provider_model_id, prompt_version, configs):
    """v0.2.2 reference recipe (no model_versions key in the preimage)."""
    return json.dumps(
        {
            "provider": provider,
            "provider_model_id": provider_model_id,
            "prompt_version": prompt_version,
            "configs": dict(configs),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _v022_reference_hash(**kwargs):
    return f"sha256:{hashlib.sha256(_v022_canonical_bytes(**kwargs)).hexdigest()}"


def _eval_outputs(rubric: Rubric, raw: float = 2.0) -> dict:
    return {
        did: {
            "raw": raw,
            "evidence_uri": f"https://evidence.local/{did}",
            "runner": HOSTED_RUNNER,
            "eval_run_id": RUN_ID,
        }
        for did in rubric.active_dim_ids
    }


def _request(rubric: Rubric, *, raw: float = 2.0, **extras) -> dict:
    body = {
        "corpus_version": "test@0.0.0",  # default; override via extras
        "tenant_id": TENANT,
        "snapshot": {
            "model_id": "acme-llm",
            "model_version": "v1.2.3",
            "prompt_version": "p7",
            "snapshot_hash": SNAPSHOT_HASH,
        },
        "eval_outputs": _eval_outputs(rubric, raw),
    }
    snap_extras = extras.pop("snapshot", None) or {}
    body["snapshot"].update(snap_extras)
    body.update(extras)
    return body


# ---------------------------------------------------------------------------
# Amendment A — scanner_version REQUIRED on ScoreResponse
# ---------------------------------------------------------------------------
class V023AmendmentAScannerVersionRequiredTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.bundles = BundleStore()

    def _call(self, body, **kw):
        return score_handler(
            body, tenant_header=TENANT, rubric=self.rubric,
            bundles=self.bundles, scored_at=SCORED_AT, **kw,
        )

    def test_response_always_carries_scanner_version(self):
        _, body = self._call(_request(self.rubric))
        self.assertIn("scanner_version", body)
        self.assertEqual(body["scanner_version"], SCANNER_VERSION)
        self.assertNotEqual(body["scanner_version"], "")

    def test_scanner_version_independent_from_rubric_version(self):
        # Build / scoring-rules separation. They may move in lockstep but must
        # not be aliased — guard against the d1832a66 conflation failure mode.
        _, body = self._call(_request(self.rubric))
        self.assertIn("rubric_version", body)
        self.assertIsNot(body["scanner_version"], body["rubric_version"])

    def test_v022_shaped_request_still_yields_scanner_version(self):
        # Caller omits all v0.2.3 fields; response still required-stamps.
        _, body = self._call(_request(self.rubric))
        self.assertEqual(body["scanner_version"], SCANNER_VERSION)


# ---------------------------------------------------------------------------
# Amendment B — corpus_version + seed server-stamped, defaulting, mismatch
# ---------------------------------------------------------------------------
class V023AmendmentBCorpusAndSeedServerStampedTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.bundles = BundleStore()

    def _call(self, body):
        return score_handler(
            body, tenant_header=TENANT, rubric=self.rubric,
            bundles=self.bundles, scored_at=SCORED_AT,
        )

    def test_seed_defaults_to_42_when_request_omits(self):
        _, body = self._call(_request(self.rubric))
        self.assertIn("seed", body)
        self.assertEqual(body["seed"], DEFAULT_SEED)
        self.assertEqual(body["seed"], 42)

    def test_seed_caller_assertion_round_trips(self):
        _, body = self._call(_request(self.rubric, seed=1337))
        self.assertEqual(body["seed"], 1337)

    def test_corpus_version_caller_assertion_round_trips(self):
        _, body = self._call(_request(self.rubric, corpus_version="halueval-qa@1.0+jbb@1.0"))
        self.assertEqual(body["corpus_version"], "halueval-qa@1.0+jbb@1.0")

    def test_corpus_version_absent_raises_scoring_error(self):
        # BEAAA-5276: score() now hard-rejects when corpus_version is None.
        # Supreme Law pin is a hard contract; silent empty string is a breach.
        # _TEST_MODE is globally True in conftest.py; force strict enforcement
        # for this specific test to verify the production raise path.
        import rubric.scoring as _sc
        from rubric.scoring import ScoringError
        rubric = self.rubric
        eval_outputs = {
            did: {"raw": 2.0, "evidence_uri": f"https://evidence.local/{did}"}
            for did in rubric.active_dim_ids
        }
        with self.assertRaisesRegex(ScoringError, "corpus_version required"):
            _sc._score_with_required_corpus_pin(
                    rubric, eval_outputs,
                    snapshot_hash=SNAPSHOT_HASH,
                    forensic_bundle_uri="https://scanner.beai.local/v0/forensic_bundle/x",
                    scored_at=SCORED_AT,
            )

    def test_corpus_version_mismatch_surfaces_in_diagnostics(self):
        # Direct invocation of score() to exercise the server-actual vs.
        # client-asserted split. In production an internal pack runner would
        # supply the actual; here we simulate explicitly.
        rubric = self.rubric
        eval_outputs = {
            did: {"raw": 2.0, "evidence_uri": f"https://evidence.local/{did}"}
            for did in rubric.active_dim_ids
        }
        result = score(
            rubric, eval_outputs,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri="https://scanner.beai.local/v0/forensic_bundle/x",
            scored_at=SCORED_AT,
            corpus_version="halueval-qa@1.0+jbb@1.0+advbench@1.0",       # actual
            client_corpus_version="halueval-qa@1.0+jbb@1.0",              # asserted
        )
        self.assertIn("diagnostics", result)
        codes = [d["code"] for d in result["diagnostics"]]
        self.assertIn("corpus_version_mismatch", codes)
        # Response stamps the actual (server-derived), not the asserted.
        self.assertEqual(result["corpus_version"], "halueval-qa@1.0+jbb@1.0+advbench@1.0")

    def test_seed_overridden_surfaces_in_diagnostics(self):
        rubric = self.rubric
        eval_outputs = {
            did: {"raw": 2.0, "evidence_uri": f"https://evidence.local/{did}"}
            for did in rubric.active_dim_ids
        }
        result = score(
            rubric, eval_outputs,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri="https://scanner.beai.local/v0/forensic_bundle/x",
            scored_at=SCORED_AT,
            corpus_version="test@0.0.0",
            seed=7,                # actual harness seed (e.g. pack-locked)
            client_seed=42,        # caller-asserted
        )
        self.assertIn("diagnostics", result)
        codes = [d["code"] for d in result["diagnostics"]]
        self.assertIn("seed_overridden", codes)
        self.assertEqual(result["seed"], 7)

    def test_no_diagnostics_when_caller_matches_server(self):
        rubric = self.rubric
        eval_outputs = {
            did: {"raw": 2.0, "evidence_uri": f"https://evidence.local/{did}"}
            for did in rubric.active_dim_ids
        }
        result = score(
            rubric, eval_outputs,
            snapshot_hash=SNAPSHOT_HASH,
            forensic_bundle_uri="https://scanner.beai.local/v0/forensic_bundle/x",
            scored_at=SCORED_AT,
            corpus_version="halueval-qa@1.0",
            client_corpus_version="halueval-qa@1.0",
            seed=42,
            client_seed=42,
        )
        self.assertNotIn("diagnostics", result)


# ---------------------------------------------------------------------------
# Amendment C — snapshot_hash canonicalization
# ---------------------------------------------------------------------------
class V023AmendmentCSnapshotHashCanonicalizationTests(unittest.TestCase):
    PROVIDER = "anthropic"
    PROVIDER_MODEL_ID = "claude-3-7-sonnet-20250219"
    PROMPT_VERSION = "p7"
    CONFIGS = {"temperature": 0.0, "max_tokens": 1024}

    def test_single_model_preimage_matches_v022_reference_bytes_for_bytes(self):
        # Regression anchor: v0.2.2 ↔ v0.2.3 replay-via-snapshot-hash.
        v022 = _v022_canonical_bytes(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs=self.CONFIGS,
        )
        v023 = canonical_preimage_bytes(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs=self.CONFIGS,
            model_versions=None,
        )
        self.assertEqual(v022, v023)

    def test_single_model_hash_matches_v022_reference(self):
        v022_hash = _v022_reference_hash(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs=self.CONFIGS,
        )
        v023_hash = canonicalize_snapshot_hash(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs=self.CONFIGS,
        )
        self.assertEqual(v022_hash, v023_hash)

    def test_empty_model_versions_treated_as_absent(self):
        # {} should reduce to v0.2.2 layout — guards against sneaky preimage
        # extension when the caller passes an empty multi-model attestation.
        v022_hash = _v022_reference_hash(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs=self.CONFIGS,
        )
        v023_hash_empty = canonicalize_snapshot_hash(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs=self.CONFIGS,
            model_versions={},
        )
        self.assertEqual(v022_hash, v023_hash_empty)

    def test_multi_model_hash_differs_from_single_model_hash(self):
        single = canonicalize_snapshot_hash(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs=self.CONFIGS,
        )
        multi = canonicalize_snapshot_hash(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs=self.CONFIGS,
            model_versions={
                "router": "gpt-4o-2025-03-01",
                "generator": "claude-3-7-sonnet-20250219",
            },
        )
        self.assertNotEqual(single, multi)

    def test_two_multi_models_differing_only_in_role_versions_have_distinct_hashes(self):
        # The whole point of Amendment C — without including model_versions
        # in the preimage these would collide and break replay.
        a = canonicalize_snapshot_hash(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs=self.CONFIGS,
            model_versions={"router": "gpt-4o-2025-03-01", "generator": "claude-3-7-sonnet-20250219"},
        )
        b = canonicalize_snapshot_hash(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs=self.CONFIGS,
            model_versions={"router": "gpt-4o-2025-03-01", "generator": "claude-3-7-sonnet-20250620"},
        )
        self.assertNotEqual(a, b)

    def test_canonicalization_is_deterministic_under_key_reordering(self):
        # Caller passes the same map in different insertion orders; sort_keys
        # in the helper must yield the same hash.
        a = canonicalize_snapshot_hash(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs={"temperature": 0.0, "max_tokens": 1024},
            model_versions={"router": "gpt-4o", "generator": "claude-3-7"},
        )
        b = canonicalize_snapshot_hash(
            provider=self.PROVIDER,
            provider_model_id=self.PROVIDER_MODEL_ID,
            prompt_version=self.PROMPT_VERSION,
            configs={"max_tokens": 1024, "temperature": 0.0},
            model_versions={"generator": "claude-3-7", "router": "gpt-4o"},
        )
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
# v0.2.3 fields round-trip via /score handler
# ---------------------------------------------------------------------------
class V023HandlerRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.bundles = BundleStore()

    def _call(self, body):
        return score_handler(
            body, tenant_header=TENANT, rubric=self.rubric,
            bundles=self.bundles, scored_at=SCORED_AT,
        )

    def test_model_versions_round_trips_through_response(self):
        body = _request(
            self.rubric,
            snapshot={
                "model_versions": {"router": "gpt-4o-2025-03-01", "generator": "claude-3-7-sonnet@1.0"},
            },
        )
        _, response = self._call(body)
        self.assertEqual(
            response.get("model_versions"),
            {"router": "gpt-4o-2025-03-01", "generator": "claude-3-7-sonnet@1.0"},
        )

    def test_model_versions_null_for_single_model_scan(self):
        _, response = self._call(_request(self.rubric))
        self.assertIn("model_versions", response)
        self.assertIsNone(response["model_versions"])

    def test_full_v023_response_carries_all_required_stamps(self):
        body = _request(
            self.rubric,
            corpus_version="halueval-qa@1.0+jbb@1.0",
            seed=99,
            snapshot={"model_versions": {"router": "gpt-4o", "generator": "claude-3-7"}},
        )
        _, response = self._call(body)
        for required in ("scanner_version", "corpus_version", "seed",
                         "snapshot_hash", "forensic_bundle_uri", "scored_at"):
            self.assertIn(required, response)
        self.assertEqual(response["scanner_version"], SCANNER_VERSION)
        self.assertEqual(response["corpus_version"], "halueval-qa@1.0+jbb@1.0")
        self.assertEqual(response["seed"], 99)
        self.assertEqual(response["model_versions"], {"router": "gpt-4o", "generator": "claude-3-7"})

    def test_request_without_seed_or_model_versions_still_stamps_defaults(self):
        # No seed, no model_versions — server defaults (seed=42, model_versions=null)
        # must still be stamped on the response. corpus_version is required
        # (BEAAA-5276) and supplied here via the _request default.
        body = _request(self.rubric)
        status, response = self._call(body)
        self.assertEqual(status, 200)
        self.assertEqual(response["scanner_version"], SCANNER_VERSION)
        self.assertEqual(response["seed"], DEFAULT_SEED)
        self.assertIn("corpus_version", response)
        self.assertIsNone(response["model_versions"])

    def test_no_diagnostics_when_request_omits_assertions(self):
        # If the caller doesn't assert anything, there's nothing to mismatch on
        # and the response must NOT include a diagnostics block.
        _, response = self._call(_request(self.rubric))
        self.assertNotIn("diagnostics", response)


if __name__ == "__main__":
    unittest.main()
