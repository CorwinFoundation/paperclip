"""BEAAA-2175 PR-B — ScoreRuntime: scoring projection wired at the API edge.

Bridges the orchestrator's :class:`scanners.live_probe_orchestrator.ScanResult`
(which carries per-dim measurement aggregates after K-replicate scanning)
into the §2.2 envelope shape that
:class:`api.scan_handlers.ScanRuntime.kickoff` persists to the scan_log
and signs with at-write-time ``snapshot_hash``.

Design (per BEAAA-2175 CTO design co-sign comment 99dbec2e):

* This module **does not** re-implement scoring math. It calls
  :func:`rubric.scoring.score` over reconstructed eval-outputs and projects
  the result through
  :func:`harness.contract_envelope.to_contract_output` — the shared library
  PR-A v2 extracted from ``harness/cli_runner.py``. The CLI and the rescan
  path therefore call **the same callee** for the contract-output projection,
  satisfying the "wrap don't fork / no logic duplication" anti-pattern in
  ``contracts/scanner-implementation-supervision-v1@v1.0``.

* :class:`ScanResult` shape is **unchanged**. ``ScoreRuntime.compute_envelope``
  reconstructs ``eval_outputs`` from ``ScanResult.dim_results``, deriving
  ``evidence_uri`` and ``evidence_sha256`` deterministically from ``dim_id``
  (matching the v0 synthetic walker at
  ``api/scan_runtime_factory.py::synthetic_v0_probe_run_fn``). When the
  walker emits fewer than the rubric's active dims (the v0 case yields
  4 of N), the remaining dims are auto-marked N/A via a synthetic
  applicability decision set so :func:`rubric.scoring.score` does not raise
  ``ScoringError("missing dim scores: ...")``.

* :class:`ScoreEnvelopeFactory` signature is **unchanged** — still
  ``(ScanResult, agent_id, run_timestamp) → dict``. The factory closure
  captures a ``ScoreRuntime`` reference and (via the runtime ref it
  already closed over for ``prior_scan_refs[]``) the
  per-scan ``ScanRequest`` + ``scanner_invocation_context`` registries the
  ``ScanRuntime`` populates at kickoff time. No factory typedef change.

* The §2.2 envelope is a **projection** of ``to_contract_output``'s output.
  The CLI projection carries 16+ fields (``cli_meta``, ``input_file_sha256s``,
  ``confidence``, ``verdict``, ``band_letter``, ``uncovered_dimensions``,
  ``contract_version``, ``model_versions`` plus the §2.2 fields); the §2.2
  envelope is a 14-field subset enumerated in ADR-0017 addendum-1 §2.2.
  ``project_to_envelope_section_2_2`` does the field selection + the
  CLI→§2.2 key remaps (``band_letter`` → ``band``).

Classification per ``contracts/scanner-implementation-supervision-v1@v1.0``:
**rule-change** — POST /scanner/v1/rescans 202 response semantics change
(``_phase1_pending`` → real values is a content-meaning change on the
contract output surface). CTO co-sign required pre-code (granted on
BEAAA-2175 comment 99dbec2e) AND pre-merge (PR-B request pending). UW
op-seat co-sign required on the v1.2.0 → v1.2.1 contract doc bump per
``contracts/are-scanner-api-v1@v1.2.0 §Approval gates``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from harness.contract_envelope import to_contract_output
from rubric.scoring import Rubric, score


#: BEAAA-2175 — synthetic applicability justification used when the v0
#: probe walker emits fewer than the rubric's active dims. The key and
#: text are byte-stable so the persisted envelope's evidence_refs N/A
#: rows JCS-canonicalize deterministically across runs.
_V0_UNCOVERED_NA_JUSTIFICATION: str = (
    "v0 synthetic probe walker does not cover this dim; replaced by a real "
    "eval-framework probe wrap in a downstream rule-execution ticket."
)
_V0_UNCOVERED_NA_JUSTIFICATION_KEY: str = "v0_synthetic_walker_uncovered"


#: ADR-0017 addendum-1 §2.2 — the 14 fields the snapshot_hash domain pins.
#: Ordered alphabetically to match the existing factory's literal-key set
#: at master ``api/scan_runtime_factory.py::_production_score_envelope_factory``;
#: JCS canonicalization sorts keys anyway, but keeping the literal sequence
#: aligned helps diff readers.
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
    documented attributes only (``agent_id``, ``governance_attestations``,
    ``file_sha256s``, ``input_dir``, ``system_prompt``, ``tool_inventory``,
    ``policy_attestations``, ``scanner_invocation_context``,
    ``applicability_decisions``). For the rescan path the CLI-edge fixtures
    (system prompt, governance attestations file, etc.) are not in scope;
    the customer's agent endpoint + the orchestrator's probe stream are the
    only inputs. We therefore supply defaults that produce a valid envelope
    without pretending to carry customer fixtures that don't exist on this
    surface. The downstream §2.2 projection strips the CLI-only fields
    (``cli_meta``, ``input_file_sha256s``, ``confidence``, ``verdict``,
    ``band_letter``, ``uncovered_dimensions``, ``contract_version``) so this
    duck-typed shape is invisible to the persisted envelope.
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
    """Re-derive the (evidence_uri, evidence_sha256) the v0 synthetic walker
    emits for ``dim_id``, byte-stably.

    Matches the rule at
    ``api/scan_runtime_factory.py::synthetic_v0_probe_run_fn`` (master
    ``6be43f1``). Lifted here so :class:`ScoreRuntime` can reconstruct
    eval_outputs from the orchestrator's ``ScanResult.dim_results`` alone
    without changing the ``DimResult`` shape or the IPC channel.
    """
    sha_hex = hashlib.sha256(dim_id.encode("utf-8")).hexdigest()
    return (
        f"https://evidence.local/{dim_id.lower()}",
        f"sha256:{sha_hex}",
    )


def _reconstruct_eval_outputs(
    dim_results: tuple[Any, ...],
) -> dict[str, dict[str, Any]]:
    """Project orchestrator ``DimResult`` tuples into the
    ``eval_outputs`` shape :func:`rubric.scoring.score` consumes.

    ``DimResult.raw`` is the median across the K-replicate runs and is the
    canonical per-dim score for the contract surface (per ``ADR-0017``
    §2.2 row "are_total / band / subscores derived from DimResult.raw").
    ``evidence_uri`` and ``evidence_sha256`` are re-derived deterministically
    from ``dim_id`` per the v0 synthetic walker rule.
    """
    out: dict[str, dict[str, Any]] = {}
    for dr in dim_results:
        evidence_uri, _evidence_sha = _derive_synthetic_evidence(dr.dim_id)
        out[dr.dim_id] = {
            "raw": float(dr.raw),
            "evidence_uri": evidence_uri,
        }
    return out


def _build_synthetic_applicability(
    rubric: Rubric, covered_dims: set[str]
) -> dict[str, dict[str, Any]]:
    """For every active rubric dim NOT in ``covered_dims``, emit an
    ``applicability_decisions`` entry marking it N/A with the byte-stable
    v0 synthetic-walker justification.

    Ensures :func:`rubric.scoring.score` does not raise
    ``ScoringError("missing dim scores: ...")`` when the v0 walker emits a
    subset of the active dims. The N/A rows flow into
    :func:`harness.contract_envelope.to_contract_output` as legitimate
    evidence_refs entries with verdict=``n/a`` (so the missing-artifact
    guard is satisfied) and into the score result's ``uncovered_dimensions``.
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

    Field mappings:
    * Direct copy: ``agent_id``, ``are_total``, ``subscores``,
      ``evidence_refs``, ``model_versions``, ``seed``, ``scanner_version``,
      ``corpus_version``, ``run_timestamp``.
    * CLI ``band_letter`` (string) → §2.2 ``band`` (string).
    * Sourced from caller args: ``purpose``, ``rubric_version``,
      ``prior_scan_refs``.
    * Defaulted ``False`` here (the rescan_handler patches them on the
      partial_completion path before persistence): ``partial_completion``,
      ``confidence_below_adjudication_threshold``.

    Fields dropped because they are CLI-only and not in §2.2:
    ``cli_meta``, ``input_file_sha256s``, ``confidence``, ``verdict``,
    ``uncovered_dimensions``, ``contract_version``, ``band_letter`` (after
    remap), plus any raw scoring keys carried in by ``score_result``
    (``bmi``, ``band`` dict, ``pillars``, ``snapshot_hash``,
    ``forensic_bundle_uri``, ``applicability_policy_version``, etc.).
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
    """API-edge scoring projection. Holds the rubric + applicability config;
    exposes :meth:`compute_envelope` to project a ``ScanResult`` into the
    ADR-0017 addendum-1 §2.2 envelope.

    Construction: callers pass a loaded :class:`Rubric`. The default
    factory at :func:`api.scan_runtime_factory.make_default_production_scan_runtime`
    loads ``rubric/rubric.yaml`` once at runtime construction and shares the
    instance across every kickoff (matching the CLI's per-run load pattern;
    the rubric file is read-only at runtime).

    The runtime is intentionally stateless across scans — every
    :meth:`compute_envelope` call receives the per-scan inputs explicitly.
    Stateless + frozen so the same instance can serve concurrent kickoffs
    safely once the orchestrator is parallelized (not in scope for PR-B).
    """

    rubric: Rubric

    @classmethod
    def from_rubric_path(cls, rubric_path: Path) -> "ScoreRuntime":
        """Load ``rubric/rubric.yaml`` (or an override path) once and return
        a :class:`ScoreRuntime` bound to it.
        """
        return cls(rubric=Rubric.load(rubric_path))

    def compute_envelope(
        self,
        *,
        scan_result: Any,
        scan_request: Any,
        agent_id: str,
        run_timestamp: str,
        invocation_context: dict[str, Any] | None,
        prior_scan_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Project a ``ScanResult`` into the §2.2 envelope.

        ``scan_result`` is :class:`scanners.live_probe_orchestrator.ScanResult`;
        ``scan_request`` is :class:`scanners.live_probe_orchestrator.ScanRequest`.
        The two attribute reads are documented:

        * ``scan_result.dim_results`` — tuple of ``DimResult`` (per-dim
          K-aggregated medians).
        * ``scan_result.scanner_version``, ``scan_result.corpus_version``
          — pinned forensic stamps.
        * ``scan_request.seed`` — RNG seed (stamped on the envelope).
        * ``invocation_context["purpose"]`` — rescan purpose
          (``pre_bind | mid_term | claim_review``).
        """
        eval_outputs = _reconstruct_eval_outputs(scan_result.dim_results)
        synth_applicability = _build_synthetic_applicability(
            self.rubric, set(eval_outputs)
        )
        snapshot_hash_stub = (
            "sha256:"
            + hashlib.sha256(
                f"rescan|{scan_result.scan_id}".encode("utf-8")
            ).hexdigest()
        )
        forensic_bundle_uri_stub = (
            f"rescan-projection://scan/{scan_result.scan_id}"
        )
        score_result = score(
            self.rubric,
            eval_outputs,
            snapshot_hash=snapshot_hash_stub,
            forensic_bundle_uri=forensic_bundle_uri_stub,
            scored_at=datetime.fromisoformat(run_timestamp.replace("Z", "+00:00"))
            if run_timestamp.endswith("Z")
            else datetime.fromisoformat(run_timestamp),
            scanner_version=scan_result.scanner_version,
            corpus_version=scan_result.corpus_version,
            seed=scan_request.seed,
            applicability_decisions=synth_applicability or None,
        )

        rescan_inputs = _build_rescan_inputs(
            agent_id=agent_id,
            invocation_context=invocation_context,
            applicability_decisions=synth_applicability or None,
        )
        cli_projection = to_contract_output(
            inputs=rescan_inputs,
            score_result=score_result,
            seed=scan_request.seed,
            corpus_version=scan_result.corpus_version,
            scanner_version=scan_result.scanner_version,
            scored_at=datetime.fromisoformat(run_timestamp.replace("Z", "+00:00"))
            if run_timestamp.endswith("Z")
            else datetime.fromisoformat(run_timestamp),
        )

        purpose = None
        if invocation_context is not None:
            raw_purpose = invocation_context.get("purpose")
            if isinstance(raw_purpose, str):
                purpose = raw_purpose

        return project_to_envelope_section_2_2(
            cli_projection,
            purpose=purpose,
            rubric_version=self.rubric.version,
            prior_scan_refs=prior_scan_refs,
        )


__all__ = [
    "ENVELOPE_FIELDS_SECTION_2_2",
    "ScoreRuntime",
    "project_to_envelope_section_2_2",
]
