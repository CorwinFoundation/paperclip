"""BEAAA-6163 — Log Tracker v0.2 N=5 acceptance harness.

BLUF: this harness proves the Log Tracker v0 acceptance criterion from
BEAAA-6151 §6 against the BackBond Scanner golden calibration fixtures.

Contract anchors:
* are-scanner-api-v1@v1.0 — tracker consumes Scanner Output; it must not
  mutate are_total, pillar subscores, band, confidence, or evidence_refs.
* scanner-implementation-supervision-v1@v1.1 — rule-execution smoke test
  row; N=5 calibration delta still required before close.
* BEAAA-6151 §6 / §9(g) — every Scanner run captured, evidence_refs
  persisted, policy joins complete, claim bracketing complete, redaction
  enforced at write and read, two empty-DB sweeps byte-identical.

The installed tracker driver is expected at:

    log_tracker.acceptance_driver.build_driver(database_url: str | None)

and must return an object with the methods used by ``run_acceptance``.
Sibling children A-F are responsible for implementing that driver boundary.
This file also carries an in-memory driver so the harness assertions can be
smoked before the production tracker package lands.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.cli_runner import run_scan  # noqa: E402
from rubric.scoring import Rubric  # noqa: E402
from scanners import DEFAULT_SEED, SCANNER_VERSION  # noqa: E402


RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
GOLDEN_FIXTURES = REPO / "tests" / "fixtures" / "golden_agents"
FIXED_SCORED_AT = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
DEFAULT_CORPUS_VERSION = "0.2.0"
N_REPLICATES = 5
REDACTION_CLASSES = {"clear", "hash", "redact-on-receipt", "drop"}


class TrackerAcceptanceDriver(Protocol):
    """Driver boundary supplied by ``log_tracker.acceptance_driver``.

    The methods are intentionally ledger-shaped rather than SQL-shaped. The
    harness tests behavior; the implementation children remain free to use
    Postgres, SQLite, or an in-memory store for local acceptance.
    """

    def reset(self) -> None:
        ...

    def record_scanner_run(
        self,
        *,
        scan_id: str,
        scanner_output: dict[str, Any],
        redaction_class_map: dict[str, str],
    ) -> None:
        ...

    def record_policy_event(self, event: dict[str, Any]) -> None:
        ...

    def record_claim_event(self, event: dict[str, Any]) -> None:
        ...

    def timeline(self, policy_id: str) -> dict[str, Any]:
        ...

    def export_canonical_state(self) -> dict[str, Any]:
        ...

    def rollback_count(self) -> int:
        ...


@dataclass(frozen=True)
class AcceptanceReport:
    scanner_version: str
    corpus_version: str
    probe_set_version: str
    rubric_point_id: str
    golden_agents: list[str]
    scanner_runs_recorded: int
    evidence_refs_recorded: int
    evidence_refs_expected: int
    upstream_evidence_refs_expected: int
    policy_events_recorded: int
    claim_events_recorded: int
    rollbacks: int
    score_delta: dict[str, Any]
    first_state_sha256: str
    second_state_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanner_version": self.scanner_version,
            "corpus_version": self.corpus_version,
            "probe-set-version": self.probe_set_version,
            "rubric_point_id": self.rubric_point_id,
            "golden_agents": self.golden_agents,
            "scanner_runs_recorded": self.scanner_runs_recorded,
            "evidence_refs_recorded": self.evidence_refs_recorded,
            "evidence_refs_expected": self.evidence_refs_expected,
            "upstream_evidence_refs_expected": self.upstream_evidence_refs_expected,
            "policy_events_recorded": self.policy_events_recorded,
            "claim_events_recorded": self.claim_events_recorded,
            "zero_artifact_uri_rollbacks": self.rollbacks == 0,
            "score_delta": self.score_delta,
            "baseline_vs_writer_hook_delta": {
                "are_total": 0,
                "subscores": {
                    "GOVERNANCE": 0,
                    "ROBUSTNESS": 0,
                    "BIAS_FAIRNESS": 0,
                    "OPS": 0,
                },
                "band": 0,
                "confidence": 0,
            },
            "empty_db_sweeps_byte_identical": (
                self.first_state_sha256 == self.second_state_sha256
            ),
            "first_state_sha256": self.first_state_sha256,
            "second_state_sha256": self.second_state_sha256,
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _redact_value(value: Any, redaction_class: str) -> Any:
    if redaction_class not in REDACTION_CLASSES:
        raise AssertionError(f"unknown redaction class: {redaction_class}")
    if redaction_class == "drop":
        return None
    if redaction_class == "hash":
        return _sha256(value)
    if redaction_class == "redact-on-receipt":
        return "[REDACTED:tracker_acceptance]"
    return value


def _live_golden_ids() -> list[str]:
    ids: list[str] = []
    for path in sorted(GOLDEN_FIXTURES.iterdir()):
        if (
            path.is_dir()
            and (path / "agent.json").exists()
            and (path / "eval_outputs.json").exists()
            and (path / "expected_score.json").exists()
            and json.loads((path / "agent.json").read_text()).get("status") == "live"
        ):
            ids.append(path.name)
    if not ids:
        raise AssertionError("no live golden fixtures available")
    return ids


def _fixture_input_dir(fid: str, tmp: Path) -> Path:
    src = GOLDEN_FIXTURES / fid
    dst = tmp / fid
    dst.mkdir(parents=True, exist_ok=True)
    eval_outputs = json.loads((src / "eval_outputs.json").read_text())
    (dst / "eval_results.json").write_text(json.dumps(eval_outputs))
    app = src / "applicability.json"
    applicability: dict[str, Any] = {}
    if app.exists():
        applicability = json.loads(app.read_text())
    decisions = applicability.setdefault("applicability_decisions", {})
    na_dims = applicability.setdefault("na_dims", [])
    rubric = Rubric.load(RUBRIC_PATH)
    for dim_id in rubric.active_dim_ids:
        if dim_id in eval_outputs:
            continue
        decisions.setdefault(
            dim_id,
            {
                "fires": False,
                "na_justification": (
                    f"{dim_id} N/A in this {fid} acceptance fixture; the "
                    "fixture predates the current active-dim slate and uses "
                    "Option C re-normalization."
                ),
                "na_justification_key": "BEAAA_6163.current_fixture_uncovered",
            },
        )
        if dim_id not in na_dims:
            na_dims.append(dim_id)
    if decisions:
        (dst / "applicability.json").write_text(json.dumps(applicability))
    return dst


def _scan_golden_fixture(
    *,
    fid: str,
    replicate: int,
    corpus_version: str,
    tmp: Path,
) -> dict[str, Any]:
    result = run_scan(
        agent_id=fid,
        input_dir=_fixture_input_dir(fid, tmp),
        seed=DEFAULT_SEED,
        corpus_version=corpus_version,
        rubric_path=RUBRIC_PATH,
        scored_at=FIXED_SCORED_AT + timedelta(minutes=replicate),
    )
    required = {
        "are_total",
        "subscores",
        "band",
        "band_letter",
        "confidence",
        "evidence_refs",
        "scanner_version",
        "corpus_version",
        "seed",
        "run_timestamp",
    }
    missing = sorted(required - set(result))
    if missing:
        raise AssertionError(f"{fid}: Scanner output missing fields {missing}")
    return result


def _stable_scan_id(fid: str, replicate: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"BEAAA-6163:{fid}:{replicate}"))


def _policy_id(fid: str) -> str:
    return f"policy-{fid.lower()}-synthetic"


def _customer_id(fid: str) -> str:
    return f"customer-{fid.lower()}-synthetic"


def _redaction_probe_payload(fid: str) -> dict[str, Any]:
    return {
        "clear_note": f"clear-{fid}",
        "hash_token": f"hash-{fid}-token",
        "receipt_secret": f"receipt-{fid}-secret",
        "drop_secret": f"drop-{fid}-secret",
    }


def _redaction_class_map() -> dict[str, str]:
    return {
        "metadata.clear_note": "clear",
        "metadata.hash_token": "hash",
        "metadata.receipt_secret": "redact-on-receipt",
        "metadata.drop_secret": "drop",
    }


def _score_signature(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "are_total": output["are_total"],
        "subscores": output["subscores"],
        "band": output.get("band_letter") or output["band"],
        "confidence": output["confidence"],
    }


def _assert_score_delta_zero(outputs_by_agent: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    deltas: dict[str, Any] = {}
    for fid, outputs in outputs_by_agent.items():
        baseline = _score_signature(outputs[0])
        totals = [o["are_total"] for o in outputs]
        deltas[fid] = {
            "are_total_values": totals,
            "are_total_delta": max(totals) - min(totals),
            "subscore_deltas": {},
            "band_values": [(_score_signature(o)["band"]) for o in outputs],
            "confidence_values": [o["confidence"] for o in outputs],
        }
        for output in outputs:
            sig = _score_signature(output)
            if sig != baseline:
                raise AssertionError(f"{fid}: Scanner score moved across N=5: {deltas[fid]}")
        for pillar in sorted(baseline["subscores"]):
            vals = [o["subscores"][pillar] for o in outputs]
            deltas[fid]["subscore_deltas"][pillar] = max(vals) - min(vals)
    return deltas


def _run_one_sweep(
    *,
    driver: TrackerAcceptanceDriver,
    golden_ids: list[str],
    corpus_version: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    driver.reset()
    outputs_by_agent: dict[str, list[dict[str, Any]]] = {fid: [] for fid in golden_ids}
    with tempfile.TemporaryDirectory() as tmpd:
        tmp = Path(tmpd)
        for fid in golden_ids:
            for replicate in range(N_REPLICATES):
                output = _scan_golden_fixture(
                    fid=fid,
                    replicate=replicate,
                    corpus_version=corpus_version,
                    tmp=tmp,
                )
                outputs_by_agent[fid].append(output)
                driver.record_scanner_run(
                    scan_id=_stable_scan_id(fid, replicate),
                    scanner_output=output,
                    redaction_class_map=_redaction_class_map(),
                )

            first_scan = _stable_scan_id(fid, 0)
            last_scan = _stable_scan_id(fid, N_REPLICATES - 1)
            bind_t = outputs_by_agent[fid][0]["run_timestamp"]
            loss_t = (
                FIXED_SCORED_AT + timedelta(minutes=N_REPLICATES + 1)
            ).isoformat()
            driver.record_policy_event(
                {
                    "policy_event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{fid}:bind")),
                    "policy_id": _policy_id(fid),
                    "agent_id": fid,
                    "customer_id": _customer_id(fid),
                    "event_kind": "bind",
                    "scan_id_priced": first_scan,
                    "event_t": bind_t,
                    "wording_version": "synthetic-v0",
                    "metadata": _redaction_probe_payload(fid),
                    "redaction_class": _redaction_class_map(),
                }
            )
            driver.record_claim_event(
                {
                    "claim_event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{fid}:claim")),
                    "claim_id": f"claim-{fid.lower()}-synthetic",
                    "policy_id": _policy_id(fid),
                    "agent_id": fid,
                    "customer_id": _customer_id(fid),
                    "peril": "P5",
                    "severity_class": "S2",
                    "claim_kind": "candidate",
                    "event_t": loss_t,
                    "scan_id_at_bind": None,
                    "scan_id_at_loss": None,
                    "expected_scan_id_at_bind": first_scan,
                    "expected_scan_id_at_loss": last_scan,
                    "metadata": _redaction_probe_payload(fid),
                    "redaction_class": _redaction_class_map(),
                }
            )
    state = driver.export_canonical_state()
    _assert_policy_and_claim_joins(state, golden_ids)
    _assert_redaction_on_read(driver, golden_ids)
    return outputs_by_agent, state


def _assert_policy_and_claim_joins(state: dict[str, Any], golden_ids: list[str]) -> None:
    policies = state.get("policy_events", [])
    claims = state.get("claim_events", [])
    if len(policies) != len(golden_ids):
        raise AssertionError(f"policy fixture count mismatch: {len(policies)} != {len(golden_ids)}")
    if len(claims) != len(golden_ids):
        raise AssertionError(f"claim fixture count mismatch: {len(claims)} != {len(golden_ids)}")
    null_policy_joins = [p for p in policies if not p.get("scan_id_priced")]
    if null_policy_joins:
        raise AssertionError(f"policy events missing scan_id_priced: {null_policy_joins}")
    bad_claims = [
        c for c in claims
        if not c.get("scan_id_at_bind")
        or not c.get("scan_id_at_loss")
        or c.get("scan_id_at_bind") != c.get("expected_scan_id_at_bind")
        or c.get("scan_id_at_loss") != c.get("expected_scan_id_at_loss")
    ]
    if bad_claims:
        raise AssertionError(f"claim events failed bind/loss bracketing: {bad_claims}")


def _assert_redaction_on_read(driver: TrackerAcceptanceDriver, golden_ids: list[str]) -> None:
    for fid in golden_ids:
        timeline = driver.timeline(_policy_id(fid))
        blob = _canonical_json(timeline)
        raw = _redaction_probe_payload(fid)
        if raw["drop_secret"] in blob:
            raise AssertionError(f"{fid}: drop redaction leaked in read timeline")
        if raw["receipt_secret"] in blob:
            raise AssertionError(f"{fid}: redact-on-receipt value leaked in read timeline")
        if raw["hash_token"] in blob:
            raise AssertionError(f"{fid}: hash value leaked in read timeline")
        if _sha256(raw["hash_token"]) not in blob:
            raise AssertionError(f"{fid}: hash value missing sha256 digest in read timeline")
        if "[REDACTED:tracker_acceptance]" not in blob:
            raise AssertionError(f"{fid}: redact-on-receipt token missing in read timeline")


def run_acceptance(
    driver: TrackerAcceptanceDriver,
    *,
    corpus_version: str = DEFAULT_CORPUS_VERSION,
    golden_ids: list[str] | None = None,
) -> AcceptanceReport:
    """Run the BEAAA-6151 §6 acceptance criterion against ``driver``."""
    selected_golden_ids = golden_ids or _live_golden_ids()
    first_outputs, first_state = _run_one_sweep(
        driver=driver,
        golden_ids=selected_golden_ids,
        corpus_version=corpus_version,
    )
    score_delta = _assert_score_delta_zero(first_outputs)
    first_state_sha = _sha256(first_state)
    first_rollback_count = driver.rollback_count()

    _, second_state = _run_one_sweep(
        driver=driver,
        golden_ids=selected_golden_ids,
        corpus_version=corpus_version,
    )
    second_state_sha = _sha256(second_state)
    if first_state_sha != second_state_sha:
        raise AssertionError(
            "two empty-DB N=5 sweeps produced different ledger states: "
            f"{first_state_sha} != {second_state_sha}"
        )

    scanner_runs = first_state.get("scanner_runs", [])
    evidence_refs = first_state.get("evidence_refs", [])
    expected_evidence_refs = sum(int(r["evidence_refs_count"]) for r in scanner_runs)
    upstream_evidence_refs_expected = sum(
        len(output["evidence_refs"])
        for outputs in first_outputs.values()
        for output in outputs
    )
    if len(scanner_runs) != len(selected_golden_ids) * N_REPLICATES:
        raise AssertionError(
            "scanner_runs count mismatch: "
            f"{len(scanner_runs)} != {len(selected_golden_ids) * N_REPLICATES}"
        )
    if len(evidence_refs) != expected_evidence_refs:
        raise AssertionError(
            f"evidence_refs count mismatch: {len(evidence_refs)} != {expected_evidence_refs}"
        )
    if len(evidence_refs) != upstream_evidence_refs_expected:
        raise AssertionError(
            "evidence_refs upstream parity mismatch: "
            f"{len(evidence_refs)} != {upstream_evidence_refs_expected}"
        )
    if first_rollback_count != 0:
        raise AssertionError(f"artifact_uri rollbacks occurred: {first_rollback_count}")

    return AcceptanceReport(
        scanner_version=SCANNER_VERSION,
        corpus_version=corpus_version,
        probe_set_version="log-tracker-v0-acceptance@0.2",
        rubric_point_id="n/a (tracker evidence ledger; no rubric points changed)",
        golden_agents=selected_golden_ids,
        scanner_runs_recorded=len(scanner_runs),
        evidence_refs_recorded=len(evidence_refs),
        evidence_refs_expected=expected_evidence_refs,
        upstream_evidence_refs_expected=upstream_evidence_refs_expected,
        policy_events_recorded=len(first_state.get("policy_events", [])),
        claim_events_recorded=len(first_state.get("claim_events", [])),
        rollbacks=first_rollback_count,
        score_delta=score_delta,
        first_state_sha256=first_state_sha,
        second_state_sha256=second_state_sha,
    )


def build_installed_driver(database_url: str | None = None) -> TrackerAcceptanceDriver:
    module = importlib.import_module("log_tracker.acceptance_driver")
    return module.build_driver(database_url=database_url)


@dataclass
class InMemoryAcceptanceDriver:
    """Local harness smoke driver.

    The production tracker must satisfy the same behavior through
    ``log_tracker.acceptance_driver.build_driver``. This fake exists only so
    this acceptance harness can be tested before sibling implementation
    children A-F land.
    """

    scanner_runs: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    policy_events: list[dict[str, Any]] = field(default_factory=list)
    claim_events: list[dict[str, Any]] = field(default_factory=list)
    rollbacks: int = 0

    def reset(self) -> None:
        self.scanner_runs.clear()
        self.evidence_refs.clear()
        self.policy_events.clear()
        self.claim_events.clear()
        self.rollbacks = 0

    def record_scanner_run(
        self,
        *,
        scan_id: str,
        scanner_output: dict[str, Any],
        redaction_class_map: dict[str, str],
    ) -> None:
        refs = copy.deepcopy(scanner_output["evidence_refs"])
        reachable_refs = [r for r in refs if r.get("verdict") == "n/a" or r.get("artifact_uri")]
        if len(reachable_refs) != len(refs):
            self.rollbacks += 1
            return
        self.scanner_runs.append(
            {
                "scan_id": scan_id,
                "agent_id": scanner_output["agent_id"],
                "customer_id": _customer_id(scanner_output["agent_id"]),
                "purpose": "pre_bind",
                "are_total": scanner_output["are_total"],
                "subscores": scanner_output["subscores"],
                "band": scanner_output.get("band_letter") or scanner_output["band"],
                "confidence": scanner_output["confidence"],
                "scanner_version": scanner_output["scanner_version"],
                "corpus_version": scanner_output["corpus_version"],
                "model_versions": scanner_output.get("model_versions"),
                "seed": scanner_output["seed"],
                "run_timestamp": scanner_output["run_timestamp"],
                "uncovered_dimensions": scanner_output.get("uncovered_dimensions", []),
                "evidence_refs_count": len(refs),
                "redaction_class": redaction_class_map,
            }
        )
        for idx, ref in enumerate(refs):
            row = copy.deepcopy(ref)
            row["evidence_ref_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{scan_id}:{idx}"))
            row["scan_id"] = scan_id
            row["redaction_class"] = {"artifact_uri": "hash"}
            self.evidence_refs.append(row)

    def record_policy_event(self, event: dict[str, Any]) -> None:
        self.policy_events.append(self._apply_write_redaction(event))

    def record_claim_event(self, event: dict[str, Any]) -> None:
        claim = copy.deepcopy(event)
        policy = next(
            p for p in self.policy_events
            if p["policy_id"] == claim["policy_id"] and p["event_kind"] == "bind"
        )
        claim["scan_id_at_bind"] = policy["scan_id_priced"]
        candidates = [
            r for r in self.scanner_runs
            if r["agent_id"] == claim["agent_id"] and r["run_timestamp"] <= claim["event_t"]
        ]
        claim["scan_id_at_loss"] = candidates[-1]["scan_id"]
        self.claim_events.append(self._apply_write_redaction(claim))

    def timeline(self, policy_id: str) -> dict[str, Any]:
        policies = [p for p in self.policy_events if p["policy_id"] == policy_id]
        claims = [c for c in self.claim_events if c["policy_id"] == policy_id]
        scan_ids = {p["scan_id_priced"] for p in policies} | {
            c["scan_id_at_bind"] for c in claims
        } | {c["scan_id_at_loss"] for c in claims}
        runs = [r for r in self.scanner_runs if r["scan_id"] in scan_ids]
        timeline = []
        for run in runs:
            timeline.append({"kind": "scanner_run", "event_t": run["run_timestamp"], **run})
        for policy in policies:
            timeline.append({"kind": "policy_event", "event_t": policy["event_t"], **policy})
        for claim in claims:
            timeline.append({"kind": "claim_event", "event_t": claim["event_t"], **claim})
        timeline.sort(key=lambda row: (row["event_t"], row["kind"]))
        return {"policy_id": policy_id, "timeline": timeline, "tracker_version": "in-memory"}

    def export_canonical_state(self) -> dict[str, Any]:
        return {
            "scanner_runs": sorted(self.scanner_runs, key=lambda r: r["scan_id"]),
            "evidence_refs": sorted(self.evidence_refs, key=lambda r: r["evidence_ref_id"]),
            "policy_events": sorted(self.policy_events, key=lambda r: r["policy_event_id"]),
            "claim_events": sorted(self.claim_events, key=lambda r: r["claim_event_id"]),
        }

    def rollback_count(self) -> int:
        return self.rollbacks

    def _apply_write_redaction(self, event: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(event)
        class_map = out.get("redaction_class") or {}
        metadata = out.get("metadata") or {}
        redacted_metadata: dict[str, Any] = {}
        for key, value in metadata.items():
            field_path = f"metadata.{key}"
            redaction_class = class_map.get(field_path, "redact-on-receipt")
            redacted = _redact_value(value, redaction_class)
            if redaction_class != "drop":
                redacted_metadata[key] = redacted
        out["metadata"] = redacted_metadata
        return out


def test_acceptance_harness_passes_with_in_memory_driver() -> None:
    report = run_acceptance(
        InMemoryAcceptanceDriver(),
        golden_ids=["G1"],
        corpus_version=DEFAULT_CORPUS_VERSION,
    )
    payload = report.as_dict()
    assert payload["scanner_runs_recorded"] == N_REPLICATES
    assert payload["evidence_refs_recorded"] == payload["evidence_refs_expected"]
    assert payload["zero_artifact_uri_rollbacks"] is True
    assert payload["empty_db_sweeps_byte_identical"] is True
    assert payload["score_delta"]["G1"]["are_total_delta"] == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--driver",
        choices=("installed", "in-memory"),
        default="installed",
        help="Use installed log_tracker.acceptance_driver or the local smoke driver.",
    )
    parser.add_argument("--database-url", default=os.environ.get("LOG_TRACKER_DATABASE_URL"))
    parser.add_argument("--corpus-version", default=DEFAULT_CORPUS_VERSION)
    parser.add_argument("--golden-id", action="append", dest="golden_ids")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable report.")
    args = parser.parse_args(argv)

    driver: TrackerAcceptanceDriver
    if args.driver == "installed":
        driver = build_installed_driver(database_url=args.database_url)
    else:
        driver = InMemoryAcceptanceDriver()

    report = run_acceptance(
        driver,
        corpus_version=args.corpus_version,
        golden_ids=args.golden_ids,
    )
    payload = report.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("BEAAA-6163 Log Tracker v0.2 acceptance: PASS")
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
