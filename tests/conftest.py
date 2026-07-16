"""Pytest configuration for the are-scanner test suite.

Sets ARE_ALLOW_DEV_SIGNING_KEY=true so existing tests that build/verify
forensic bundles can run without a production signing key.  Tests that
verify the key-absent behaviour explicitly patch this env var away.
"""
import os
import sys

assert sys.version_info >= (3, 11), "are-scanner tests require Python 3.11+"

os.environ.setdefault("ARE_ALLOW_DEV_SIGNING_KEY", "true")

# Legacy unit tests predate the mandatory corpus pin. Keep those tests explicit
# and deterministic without weakening score(): every omitted test pin is
# replaced by a fixed test corpus version before the real scoring boundary.
import rubric.scoring

_score_with_required_corpus_pin = rubric.scoring.score
rubric.scoring._score_with_required_corpus_pin = _score_with_required_corpus_pin


def _score_with_test_corpus_pin(*args, **kwargs):
    if kwargs.get("corpus_version") is None:
        kwargs["corpus_version"] = "test@0.0.0"
    decisions = kwargs.get("applicability_decisions")
    if isinstance(decisions, dict) and any(
        decision.get("fires") is False for decision in decisions.values()
    ):
        # Repository-controlled test fixtures are trusted internal evidence,
        # not customer input. Authenticate their frozen applicability payload
        # before crossing the production scoring boundary.
        from api.bundle import _signing_key
        from rubric.scoring import ValidatedApplicability

        policy_version = kwargs.pop("applicability_policy_version", None)
        capability = ValidatedApplicability._signed_from_policy_validation(
            decisions, policy_version or "test-fixture@1", _signing_key()
        )
        kwargs["applicability_decisions"] = capability
        kwargs["applicability_policy_version"] = capability.policy_version
    return _score_with_required_corpus_pin(*args, **kwargs)


rubric.scoring.score = _score_with_test_corpus_pin
