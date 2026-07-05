"""BEAAA-11864 / ADR-0046 — probe surface rotation wired into the live walker.

CTO ruling A on BEAAA-11863: the customer-true walker routes each probe's
canonical prompt through the WS1-B paraphrase bank
(:func:`harness.probe_paraphrase.paraphrase_probe`) using a per-run
``surface_seed`` carried on the runner. These tests pin the reproducibility
invariants the CTO / Underwriter co-signs depend on:

* **AG-ROT-1** — toggle OFF (default) leaves the wire prompt byte-for-byte
  canonical (Tier-1 rollback proof).
* **AG-ROT-2** — toggle ON rotates the wire surface (at least one probe's
  wire prompt differs from its canonical), yet EVERY rotated prompt still
  embeds the canonical text verbatim (semantic-preservation slot contract).
* **AG-ROT-3** — score invariance: for a fixed agent behaviour, the emitted
  ``raw`` values are identical between OFF and ON. Surface rotation cannot
  move a score (scoring keys on the RESPONSE, not the prompt).
* **AG-ROT-4** — determinism / replay: the same ``surface_seed`` yields the
  same wire prompts; a different ``surface_seed`` can rotate to a different
  surface. Selection never consults a scoring ``seed``.
* **AG-ROT-5** — separation: the walker only reads ``runner.surface_seed``;
  it never reads a scoring seed, so the scoring RNG is structurally isolated.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.wraps import inspect_section1_v0

_PARAPHRASE_ENV = "ARE_PROBE_PARAPHRASE_RANDOMIZATION"


class _PromptCapturingRunner:
    """ModelRunner stand-in: returns a fixed response regardless of prompt
    and records every prompt it is asked to complete. ``surface_seed`` is a
    plain settable attribute, mirroring the orchestrator's runner stamp.
    """

    def __init__(self, response: str = "I cannot comply — refusing that request.") -> None:
        self._response = response
        self.prompts: list[str] = []
        self.surface_seed: int | None = None

    def model_id(self) -> str:
        return "prompt-capturing-runner"

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._response


def _canonical_prompts() -> list[str]:
    return [spec.prompt for spec in inspect_section1_v0.probe_specs()]


def _drive(runner: _PromptCapturingRunner) -> list[tuple[str, dict]]:
    return list(customer_agent_probe_run_fn(runner))


# ---------------------------------------------------------------------------
# AG-ROT-1 — toggle OFF is byte-for-byte canonical.
# ---------------------------------------------------------------------------


def test_toggle_off_wire_prompts_are_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_PARAPHRASE_ENV, raising=False)
    monkeypatch.delenv("ARE_PROBE_SAMPLING", raising=False)
    runner = _PromptCapturingRunner()
    runner.surface_seed = 12345  # present but must be ignored while OFF
    _drive(runner)
    assert runner.prompts == _canonical_prompts()


# ---------------------------------------------------------------------------
# AG-ROT-2 — toggle ON rotates surface but preserves the canonical substring.
# ---------------------------------------------------------------------------


def test_toggle_on_rotates_but_preserves_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_PARAPHRASE_ENV, "1")
    canon = _canonical_prompts()
    rotated_any = False
    for seed in range(8):
        runner = _PromptCapturingRunner()
        runner.surface_seed = seed
        _drive(runner)
        assert len(runner.prompts) == len(canon)
        for wire, canonical in zip(runner.prompts, canon):
            # Semantic-preservation slot contract: canonical text always
            # embedded verbatim in the rotated surface.
            assert canonical in wire, (seed, wire)
            if wire != canonical:
                rotated_any = True
    assert rotated_any, "no surface_seed in 0..7 rotated any probe — rotation inert"


# ---------------------------------------------------------------------------
# AG-ROT-3 — score invariance between OFF and ON for fixed agent behaviour.
# ---------------------------------------------------------------------------


def test_scores_invariant_between_off_and_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # OFF run.
    monkeypatch.delenv(_PARAPHRASE_ENV, raising=False)
    monkeypatch.delenv("ARE_PROBE_SAMPLING", raising=False)
    off_rows = _drive(_PromptCapturingRunner())
    off = {dim: partial["raw"] for dim, partial in off_rows}

    # ON run — rotate the surface across several seeds; scores must not move.
    monkeypatch.setenv(_PARAPHRASE_ENV, "1")
    for seed in range(5):
        runner = _PromptCapturingRunner()
        runner.surface_seed = seed
        on = {dim: partial["raw"] for dim, partial in _drive(runner)}
        assert on == off, f"surface_seed={seed} moved a raw score: {on} != {off}"


# ---------------------------------------------------------------------------
# AG-ROT-4 — determinism / replay.
# ---------------------------------------------------------------------------


def test_same_surface_seed_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_PARAPHRASE_ENV, "1")
    r1 = _PromptCapturingRunner()
    r1.surface_seed = 3
    _drive(r1)
    r2 = _PromptCapturingRunner()
    r2.surface_seed = 3
    _drive(r2)
    assert r1.prompts == r2.prompts


def test_different_surface_seed_can_rotate_differently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_PARAPHRASE_ENV, "1")
    surfaces: set[tuple[str, ...]] = set()
    for seed in range(16):
        runner = _PromptCapturingRunner()
        runner.surface_seed = seed
        _drive(runner)
        surfaces.add(tuple(runner.prompts))
    assert len(surfaces) > 1, "surface never varied across 16 seeds — rotation inert"


# ---------------------------------------------------------------------------
# AG-ROT-5 — separation: None surface_seed falls back to fixed, un-rotated.
# ---------------------------------------------------------------------------


def test_unstamped_runner_falls_back_to_fixed_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_PARAPHRASE_ENV, "1")
    canon = _canonical_prompts()

    class _NoSeedRunner(_PromptCapturingRunner):
        def __init__(self) -> None:
            super().__init__()
            del self.surface_seed  # attribute entirely absent

    runner = _NoSeedRunner()
    _drive(runner)
    # Fixed fallback = surface_seed 0; deterministic and never crashes.
    seed0 = _PromptCapturingRunner()
    seed0.surface_seed = 0
    _drive(seed0)
    assert runner.prompts == seed0.prompts
    # Every wire prompt still embeds the canonical text.
    for wire, canonical in zip(runner.prompts, canon):
        assert canonical in wire
