"""BEAAA-2182 Track A — Scorer-side scoring projection.

Replaces the (now deleted) ``api/score_runtime.py`` from the BEAAA-2175
PR-B lineage. Lives in the ``scanners/`` namespace so the scoring callee
``rubric.scoring.score`` is **not** transitively reachable from
``api/`` route handlers — see ``scripts/check_no_apiedge_score.py``
(AG-A5 CI gate) and the Scorer-startup runtime assertion in
``scanners/live_probe_workers.py::run_scorer``.

Design (per CTO design co-sign on BEAAA-2182 wake comment ``9840343c``):

* Module is imported only by the Scorer subprocess (post-fork). The
  parent process (API edge) pre-loads ``Rubric.load(rubric_path)`` and
  passes the byte-stable instance into ``spawn_scorer`` via
  fork-inherited memory (Q-A1 ACCEPT).
* :class:`ScoreRuntime` is intentionally frozen / stateless across
  scans. Every :meth:`compute_envelope` call receives the per-scan
  inputs explicitly so concurrent kickoffs (out of scope for Track A;
  in scope for a future orchestrator parallelization ticket) share the
  same instance safely.
* :meth:`compute_envelope` returns the ADR-0017 addendum-1 §2.2
  envelope dict — the same shape the deleted ``api/score_runtime.py``
  produced (frozen at v1.2.1 contract; v1.5.0 contract patch adds the
  Scorer→API IPC surface around it without changing the dict shape).
* Calls into ``rubric.scoring.score`` (math) and
  ``harness.contract_envelope.to_contract_output`` (CLI-shape projection
  PR-A v2 extracted from ``harness/cli_runner.py``). The CLI offline
  path and the Track A rescan path now call the same two callees, so
  AG-A2 N=5 calibration spread = 0 is the binding gate.
* When the v0 synthetic walker emits fewer than the rubric's active
  dims (4 of N today), the remaining dims are auto-marked N/A with a
  byte-stable justification (``_V0_UNCOVERED_NA_JUSTIFICATION``) so
  :func:`rubric.scoring.score` does not raise
  ``ScoringError("missing dim scores: ...")``. Identical rule to the
  deleted ``api/score_runtime.py`` lineage; preserved byte-equally so
  the AG-A2 calibration spread holds.

Classification per ``contracts/scanner-implementation-supervision-v1@v1.0``:
**rule-change** — Scorer→API IPC surface change. CTO co-sign on design
granted on BEAAA-2182 comment ``9840343c``; CTO co-sign on merge AND
UW op-seat co-sign on the v1.5.0 contract patch are pre-merge gates.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.contract_envelope import to_contract_output
from harness.applicability import load_policy, validate_for_scoring
from rubric.scoring import Rubric, ValidatedApplicability, score
from api.bundle import _signing_key


#: BEAAA-2175 — synthetic applicability justification used when the v0
#: probe walker emits fewer than the rubric's active dims. Lifted
#: byte-equally from the deleted ``api/score_runtime.py`` so the
#: persisted envelope's evidence_refs N/A rows JCS-canonicalize
#: deterministically across runs and AG-A2 spread = 0 holds.
_V0_UNCOVERED_NA_JUSTIFICATION: str = (
    "v0 synthetic probe walker does not cover this dim; replaced by a real "
    "eval-framework probe wrap in a downstream rule-execution ticket."
)
_V0_UNCOVERED_NA_JUSTIFICATION_KEY: str = "v0_synthetic_walker_uncovered"


#: ADR-0017 addendum-1 §2.2 — the 14 fields the snapshot_hash domain pins.
#: Re-declared here so the Scorer subprocess can validate the projected
#: dict against the closed allow-list mirror in
#: ``scanners/live_probe_workers.py::ALLOWED_SCORE_ENVELOPE_FIELDS``
#: before placing it on the IPC return channel.
ENVELOPE_FIELDS_SECTION_2_2: tuple[str, ...] = (
    "agent_id",
    "are_total",
    "band",
    "confidence_below_adjudication_threshold",
    "corpus_version",
    "evidence_refs",
    "model_versions",
    "partial_completion",
    "prior_scan_refs",
    "purpose",
    "rubric_version",
    "run_timestamp",
    "scanner_version",
    "seed",
    "subscores",
)


@dataclass(frozen=True)
class _RescanProjectionInputs:
    """Duck-typed ``CliInputs`` stand-in for the rescan path.

    :func:`harness.contract_envelope.to_contract_output` reaches into
    documented attributes only. The rescan path has no CLI-edge fixtures
    (system prompt file, governance attestations file, etc.) — the
    customer's agent endpoint + the orchestrator's probe stream are the
    only inputs. Defaults below produce a valid envelope without
    fabricating fixtures that don't exist; the §2.2 projection downstream
    strips the CLI-only fields so this shape is invisible to the
    persisted envelope.
    """

    agent_id: str
    file_sha256s: dict[str, str]
    input_dir: Path
    system_prompt: str | None
    tool_inventory: list[dict[str, Any]] | None
    policy_attestations: dict[str, Any] | None
    governance_attestations: dict[str, Any] | None
    scanner_invocation_context: dict[str, Any] | None
    applicability_decisions: dict[str, dict[str, Any]] | None


def _derive_synthetic_evidence(dim_id: str) -> tuple[str, str]:
    """Re-derive the (evidence_uri, evidence_sha256) the v0 synthetic
    walker emits for ``dim_id``, byte-stably.

    Matches the rule at
    ``api/scan_runtime_factory.py::synthetic_v0_probe_run_fn`` (master
    ``52c003e``). Lifted here so :class:`ScoreRuntime` can reconstruct
    eval_outputs from the IPC-validated row stream alone, without
    changing the ``DimResult`` / IPC row shape.
    """
    sha_hex = hashlib.sha256(dim_id.encode("utf-8")).hexdigest()
    return (
        f"https://evidence.local/{dim_id.lower()}",
        f"sha256:{sha_hex}",
    )


def _reconstruct_eval_outputs(
    accumulated_rows: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Project the Scorer-accumulated row map into the ``eval_outputs``
    shape :func:`rubric.scoring.score` consumes.

    The Scorer receives one row per dim_id from the Prober via
    :func:`scanners.live_probe_workers.validate_inbound`. Each row
    already passed the closed ALLOWED_ROW_FIELDS allow-list and the
    intake-guard validators (provenance + shape-safety). This function
    selects ``raw`` (the canonical per-dim score) and ``evidence_uri``;
    the scoring engine ignores other fields.

    The ``evidence_uri`` falls back to the synthetic walker's
    deterministic rule when the row's value is empty (e.g., a future
    real probe wrap might not emit an evidence URI on the IPC row but
    deliver it via the evidence bundle URI).
    """
    out: dict[str, dict[str, Any]] = {}
    for dim_id, row in accumulated_rows.items():
        evidence_uri = row.get("evidence_uri") or ""
        if not evidence_uri:
            evidence_uri, _ = _derive_synthetic_evidence(dim_id)
        out[dim_id] = {
            "raw": float(row["raw"]),
            "evidence_uri": evidence_uri,
        }
    return out


def _has_uncovered_fires_true(
    decisions: dict[str, dict[str, Any]] | None, covered_dims: set[str]
) -> bool:
    """BEAAA-5414 Q-B4 — return True iff any applicability decision
    marks ``fires=True`` for a dim NOT present in ``covered_dims``.

    CASE 1 (synthetic_v0 path) calls :func:`_build_synthetic_applicability`
    which marks every uncovered active dim with ``fires=False``; the
    iteration below short-circuits False on that input. Only the
    CASE 2 customer-supplied applicability path can return True here —
    which is the only path that can flip ``partial_completion`` to True
    via :meth:`ScoreRuntime.compute_envelope`.
    """
    if not decisions:
        return False
    for did, decision in decisions.items():
        if decision.get("fires") is True and did not in covered_dims:
            return True
    return False


def _build_synthetic_applicability(
    rubric: Rubric, covered_dims: set[str]
) -> dict[str, dict[str, Any]]:
    """For every active rubric dim NOT in ``covered_dims``, emit an
    ``applicability_decisions`` entry marking it N/A with the byte-stable
    v0 synthetic-walker justification.

    Ensures :func:`rubric.scoring.score` does not raise
    ``ScoringError("missing dim scores: ...")`` when the v0 walker emits
    a subset of the active dims. The N/A rows flow into
    :func:`harness.contract_envelope.to_contract_output` as legitimate
    evidence_refs entries with verdict=``n/a`` (so the missing-artifact
    guard is satisfied) and into the score result's
    ``uncovered_dimensions``.
    """
    return {
        did: {
            "fires": False,
            "na_justification": _V0_UNCOVERED_NA_JUSTIFICATION,
            "na_justification_key": _V0_UNCOVERED_NA_JUSTIFICATION_KEY,
        }
        for did in rubric.active_dim_ids
        if did not in covered_dims
    }


def _build_rescan_inputs(
    *,
    agent_id: str,
    invocation_context: dict[str, Any] | None,
    applicability_decisions: dict[str, dict[str, Any]],
) -> _RescanProjectionInputs:
    """Construct the duck-typed inputs object the
    :func:`harness.contract_envelope.to_contract_output` projection reads
    from. ``input_dir`` is a placeholder ``Path("rescan")`` so the
    projection's ``cli_meta.input_dir_name`` field (stripped by the §2.2
    selector downstream) has something to render; no file I/O is performed.
    """
    return _RescanProjectionInputs(
        agent_id=agent_id,
        file_sha256s={},
        input_dir=Path("rescan"),
        system_prompt=None,
        tool_inventory=None,
        policy_attestations=None,
        governance_attestations=None,
        scanner_invocation_context=invocation_context,
        applicability_decisions=applicability_decisions,
    )


def project_to_envelope_section_2_2(
    cli_projection: dict[str, Any],
    *,
    purpose: str | None,
    rubric_version: str,
    prior_scan_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reduce the CLI-shape contract output to the ADR-0017 addendum-1
    §2.2 envelope (14 fields).

    Field mappings — byte-equal to the deleted ``api/score_runtime.py``
    function of the same name (AG-A2 spread = 0 depends on this):
    * Direct copy: ``agent_id``, ``are_total``, ``subscores``,
      ``evidence_refs``, ``model_versions``, ``seed``, ``scanner_version``,
      ``corpus_version``, ``run_timestamp``.
    * CLI ``band_letter`` (string) → §2.2 ``band`` (string).
    * Sourced from caller args: ``purpose``, ``rubric_version``,
      ``prior_scan_refs``.
    * Defaulted ``False`` here (the rescan_handler patches them on the
      partial_completion path before persistence):
      ``partial_completion``, ``confidence_below_adjudication_threshold``.
    """
    envelope: dict[str, Any] = {
        "agent_id": cli_projection["agent_id"],
        "are_total": cli_projection["are_total"],
        "band": cli_projection["band_letter"],
        "confidence_below_adjudication_threshold": False,
        "corpus_version": cli_projection["corpus_version"],
        "evidence_refs": cli_projection["evidence_refs"],
        "model_versions": cli_projection.get("model_versions"),
        "partial_completion": False,
        "prior_scan_refs": prior_scan_refs,
        "purpose": purpose,
        "rubric_version": rubric_version,
        "run_timestamp": cli_projection["run_timestamp"],
        "scanner_version": cli_projection["scanner_version"],
        "seed": cli_projection["seed"],
        "subscores": cli_projection["subscores"],
    }
    return envelope


@dataclass(frozen=True)
class ScoreRuntime:
    """Scorer-side scoring projection. Holds the rubric (fork-inherited
    from the API-edge parent) and exposes :meth:`compute_envelope` to
    project the Scorer's accumulated row stream into the §2.2 envelope.

    Construction: API-edge parent calls
    :meth:`ScoreRuntime.from_rubric_path` once at runtime construction
    time (BEFORE fork), then passes ``self`` into ``spawn_scorer`` via a
    keyword argument the fork inherits. No per-scan YAML parse — Q-A1
    ACCEPT.
    """

    rubric: Rubric

    @classmethod
    def from_rubric_path(cls, rubric_path: Path) -> "ScoreRuntime":
        """Load ``rubric/rubric.yaml`` (or an override path) once and
        return a :class:`ScoreRuntime` bound to it.
        """
        return cls(rubric=Rubric.load(rubric_path))

    def compute_envelope(
        self,
        *,
        accumulated_rows: dict[str, dict[str, Any]],
        scan_id: str,
        agent_id: str,
        seed: int,
        scanner_version: str,
        corpus_version: str,
        run_timestamp: str,
        invocation_context: dict[str, Any] | None,
        prior_scan_refs: list[dict[str, Any]],
        applicability_decisions: dict[str, dict[str, Any]] | None = None,
        walker_partial_completion: bool = False,
    ) -> dict[str, Any]:
        """Project the accumulated IPC row map into the §2.2 envelope.

        Called by the Scorer subprocess on END_OF_SCAN, after every
        validated row has been accumulated. The return value is
        serialized through
        :func:`scanners.live_probe_workers.serialize_score_envelope`
        (closed ALLOWED_SCORE_ENVELOPE_FIELDS allow-list) before
        crossing the IPC return channel back to the API-edge parent.

        Arguments:
        * ``accumulated_rows`` — dict the Scorer's ``on_validated_row``
          callback built up (one entry per dim_id with IPC-row fields
          ``{raw, evidence_uri, evidence_sha256, runner, eval_run_id}``).
        * ``scan_id``, ``agent_id``, ``seed``, ``scanner_version``,
          ``corpus_version`` — forensic stamps fork-inherited from the
          parent's ``spawn_scorer(score_inputs=...)`` kwargs.
        * ``run_timestamp`` — ISO-8601 timestamp the parent stamped at
          spawn time. The factory closure on the API edge may patch
          this to match the kickoff timestamp; this method writes
          whatever it receives so the parent-side patch is idempotent.
        * ``invocation_context`` — rescan_handler payload carrying
          ``purpose`` (``pre_bind | mid_term | claim_review``); the
          BEAAA-5414 customer-true Track B walker additionally carries
          ``applicability_decisions`` here when CASE 2 is selected.
        * ``prior_scan_refs`` — typically empty on the Scorer side; the
          API-edge factory closure injects the real chain post-receive
          (the scan_log lookup is API-edge state).
        * ``applicability_decisions`` — explicit kwarg override
          (CLI/golden path). BEAAA-5414 customer-true path leaves this
          unset and instead consumes decisions from
          ``invocation_context["applicability_decisions"]`` so the
          production ``score_inputs`` literal at
          ``api/scan_runtime_factory.py`` stays inside the BEAAA-2182
          Q-A4 pinned key set (regression
          ``TestProductionScoreInputsAreCase1`` enforces this).
        * ``walker_partial_completion`` — BEAAA-5414 Q-B4 caller
          override; when ``True`` the projected envelope's
          ``partial_completion`` field is forced ``True`` regardless of
          accumulated-row coverage. Used on the customer-true path
          when the API-edge closure detects the walker raised under
          ``MAX_WALL_CLOCK_SECONDS`` or similar. Default ``False``
          preserves the synthetic_v0 path byte-stability.
        """
        eval_outputs = _reconstruct_eval_outputs(accumulated_rows)
        if applicability_decisions is None and invocation_context is not None:
            # BEAAA-5414 Q-B3 — customer-true Track B walker carries
            # ``applicability_decisions`` through ``invocation_context``
            # so the production ``score_inputs`` literal stays inside
            # the BEAAA-2182 Q-A4 pinned key set. CTO co-sign BEAAA-5418
            # + UW op-seat co-sign BEAAA-5420 cover this provenance dial
            # CASE 1 → CASE 2 flip on the v1.5.0 contract patch.
            ctx_decisions = invocation_context.get("applicability_decisions")
            if isinstance(ctx_decisions, dict):
                applicability_decisions = ctx_decisions
        if applicability_decisions is not None:
            trusted_flags = (
                invocation_context.get("architecture_flags")
                if invocation_context is not None else None
            )
            if not isinstance(trusted_flags, dict):
                raise ValueError(
                    "architecture_flags required to validate applicability decisions"
                )
            synth_applicability = validate_for_scoring(
                load_policy(),
                trusted_flags,
                applicability_decisions,
                self.rubric.active_dim_ids,
                _signing_key(),
            )
            applicability_for_envelope = dict(synth_applicability.decisions)
        else:
            # Synthetic-walker path — auto-mark active dims absent from
            # eval_outputs as N/A with the byte-stable v0 justification. This
            # is scorer-owned compatibility policy, never caller authority;
            # authenticate the derived capability before score() consumes it.
            derived = _build_synthetic_applicability(
                self.rubric, set(eval_outputs)
            )
            synth_applicability = ValidatedApplicability._signed_from_policy_validation(
                derived, "synthetic-v0@1", _signing_key()
            )
            applicability_for_envelope = derived
        snapshot_hash_stub = (
            "sha256:"
            + hashlib.sha256(
                f"rescan|{scan_id}".encode("utf-8")
            ).hexdigest()
        )
        forensic_bundle_uri_stub = (
            f"rescan-projection://scan/{scan_id}"
        )
        score_result = score(
            self.rubric,
            eval_outputs,
            snapshot_hash=snapshot_hash_stub,
            forensic_bundle_uri=forensic_bundle_uri_stub,
            scored_at=_parse_run_timestamp(run_timestamp),
            scanner_version=scanner_version,
            corpus_version=corpus_version,
            seed=seed,
            applicability_decisions=synth_applicability,
        )

        rescan_inputs = _build_rescan_inputs(
            agent_id=agent_id,
            invocation_context=invocation_context,
            applicability_decisions=applicability_for_envelope,
        )
        cli_projection = to_contract_output(
            inputs=rescan_inputs,
            score_result=score_result,
            seed=seed,
            corpus_version=corpus_version,
            scanner_version=scanner_version,
            scored_at=_parse_run_timestamp(run_timestamp),
        )

        purpose: str | None = None
        if invocation_context is not None:
            raw_purpose = invocation_context.get("purpose")
            if isinstance(raw_purpose, str):
                purpose = raw_purpose

        envelope = project_to_envelope_section_2_2(
            cli_projection,
            purpose=purpose,
            rubric_version=self.rubric.version,
            prior_scan_refs=prior_scan_refs,
        )

        # BEAAA-5414 Q-B4 — partial_completion single source of truth.
        # True when (a) the API-edge caller explicitly signals incomplete
        # walker execution (timeout / runner raise) via the kwarg, or
        # (b) any ``fires=True`` applicability dim is uncovered after
        # walker completion. CASE 1 (synthetic_v0) auto-synthesizes all
        # uncovered active dims with ``fires=False``, so this is False
        # on the synthetic_v0 path — byte-stability preserved.
        if walker_partial_completion or _has_uncovered_fires_true(
            applicability_for_envelope, set(eval_outputs)
        ):
            envelope["partial_completion"] = True

        return envelope


def _parse_run_timestamp(run_timestamp: str) -> datetime:
    """Parse an ISO-8601 timestamp; accept ``...Z`` suffix as UTC."""
    if run_timestamp.endswith("Z"):
        return datetime.fromisoformat(run_timestamp.replace("Z", "+00:00"))
    return datetime.fromisoformat(run_timestamp)


__all__ = [
    "ENVELOPE_FIELDS_SECTION_2_2",
    "ScoreRuntime",
    "project_to_envelope_section_2_2",
]
