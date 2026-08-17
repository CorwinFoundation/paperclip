#!/usr/bin/env python3
"""Founder-Dev-Mode promotion authorization (BEAAA-WAVE1B-DRAFT).

Development-stage counterpart to the production ed25519 activation gate in
``evals/aiuc_path_b/wave1a_candidate.py``. It lets a family be *scored*
(underwriting-eligibility, zero score weight) during founder development
WITHOUT an out-of-band production-authority signature — but only when the
operator explicitly opts in via the environment, and it FAILS CLOSED at the
production boundary.

Contract
--------
* A family opts into dev-mode scoring in its probe-mapping ``binding``::

      attestation_tier: "founder_dev_mode"
      dev_scoring_active: true

  It leaves ``binding.scoring_active`` (the production-signed flag) ``false``,
  so nothing reads it as production-attested.
* ``BACKBOND_FOUNDER_DEV_MODE=on`` turns dev-mode scoring on. Unset/``off`` =>
  the family resolves to reported-not-scored, so a production-default build
  behaves exactly as it did before promotion (no silent expansion of the
  scored / underwriting surface).
* Dev-mode evidence is never production attestation. ``assert_production_attested``
  raises if any *scored* row carries the ``founder_dev_mode`` tier, so a
  customer-facing / production emission path cannot ship dev-promoted scoring.
  Promotion to production still requires the real signature + underwriting
  co-sign + QA exact-SHA gate.
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Mapping

FOUNDER_DEV_MODE_ENV = "BACKBOND_FOUNDER_DEV_MODE"

ATTESTATION_PRODUCTION_SIGNED = "production_signed"
ATTESTATION_FOUNDER_DEV = "founder_dev_mode"
ATTESTATION_TIERS = frozenset({ATTESTATION_PRODUCTION_SIGNED, ATTESTATION_FOUNDER_DEV})

_TRUTHY = {"on", "1", "true", "yes"}
_FALSY = {"", "off", "0", "false", "no"}


class FounderDevModeError(RuntimeError):
    """Raised on invalid opt-in, or when dev-scored evidence hits production."""


def is_dev_mode(env: Mapping[str, str] | None = None) -> bool:
    """True iff the founder-dev-mode opt-in is explicitly set.

    Fails closed: an unrecognised value raises rather than guessing, mirroring
    the strict rollback-override convention in ``tools/tranche1_observational``.
    """
    raw = (env if env is not None else os.environ).get(FOUNDER_DEV_MODE_ENV, "")
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    raise FounderDevModeError(f"invalid {FOUNDER_DEV_MODE_ENV}={raw!r}")


def _binding(data: Mapping[str, Any]) -> Mapping[str, Any]:
    binding = data.get("binding")
    return binding if isinstance(binding, Mapping) else {}


def attestation_tier(data: Mapping[str, Any]) -> str:
    """Attestation tier declared by a probe mapping; production_signed default."""
    binding = _binding(data)
    tier = binding.get("attestation_tier") or data.get("attestation_tier")
    if tier is None:
        return ATTESTATION_PRODUCTION_SIGNED
    tier = str(tier)
    if tier not in ATTESTATION_TIERS:
        raise FounderDevModeError(f"unknown attestation_tier {tier!r}")
    return tier


def dev_scoring_declared(data: Mapping[str, Any]) -> bool:
    """True iff the mapping declares dev-mode scoring intent."""
    binding = _binding(data)
    return binding.get("dev_scoring_active") is True or data.get("dev_scoring_active") is True


def assert_production_attested(rows: Iterable[Mapping[str, Any]]) -> None:
    """Fail closed if any *scored* row is only founder-dev attested.

    Call this at production / customer-facing emission boundaries (certificate
    generation, published-count reconciliation, production scan mode). A family
    promoted under founder-dev-mode must be re-attested with a production
    signature before it can cross this line.
    """
    offenders = sorted(
        {
            str(row.get("test_id") or "")
            for row in rows
            if row.get("scoring_active") is True
            and str(row.get("attestation_tier") or ATTESTATION_PRODUCTION_SIGNED)
            == ATTESTATION_FOUNDER_DEV
        }
    )
    if offenders:
        raise FounderDevModeError(
            "dev-mode-scored families cannot cross the production boundary "
            f"without production attestation: {', '.join(offenders)}"
        )


def assert_production_emission_allowed(env: Mapping[str, str] | None = None) -> None:
    """Cheap fail-closed guard for customer / production emission hot paths.

    Use at boundaries that produce customer-facing or published artifacts but do
    not already hold the registry rows (e.g. the per-response customer payload),
    where rebuilding the registry to call ``assert_production_attested`` would be
    wasteful. A founder-dev-mode session must never emit a customer/production
    artifact, so this refuses whenever dev-mode is active. In production
    (flag off) it is a no-op.
    """
    if is_dev_mode(env):
        raise FounderDevModeError(
            "founder-dev-mode is active; customer/production emission is blocked "
            "until dev-mode-scored families are re-attested with a production signature"
        )
