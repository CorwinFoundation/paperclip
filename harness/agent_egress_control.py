"""Disabled-by-default, control-only agent-egress-bench harness.

This module evaluates customer security controls, never agent behavior. It has
no model clients and accepts only a local adapter implementing ``evaluate``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Any, Mapping, Sequence

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

FEATURE_FLAG = "BACKBOND_AGENT_EGRESS_CONTROL"
SOURCE_PROJECT = "luckyPipewrench/agent-egress-bench"
SOURCE_COMMIT = "e19efee4894994c887f6ae69bcdfbc05b93b56db"
CASES_TREE = "d1d50c8d3e5a920311c53686f0d2fc3fdbe33d33"
CASES_SHA256 = "sha256:8a9a6d99c4573cad1bfd66b0f6264db9e30a3694c09587c21edb15ba2009645e"
ORACLE_VERSION = "agent-egress-control-oracle-v1"
RECEIPT_VERSION = "agent-egress-control-receipt-v2"
EVIDENCE_SCHEMA_VERSION = "agent-egress-control-evidence-v1"
SIGNATURE_ALGORITHM = "ed25519"
OUTCOMES = ("passed", "failed", "errored", "not_applicable")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
ERROR_CODES = frozenset({"evaluation_error", "timeout", "transport_error"})


class ReceiptBoundaryError(ValueError):
    """Closed, machine-auditable failure at the receipt boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class ApplicabilityInputs:
    supported_transports: frozenset[str]
    capabilities: frozenset[str]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApplicabilityInputs":
        transports = value.get("supported_transports", ())
        capabilities = value.get("capabilities", ())
        if "control_type" in value:
            raise ValueError(
                "control_type is not an applicability input; declare transports "
                "and capabilities supported by the control"
            )
        if not isinstance(transports, Sequence) or isinstance(transports, (str, bytes)):
            raise ValueError("supported_transports must be a list")
        if not isinstance(capabilities, Sequence) or isinstance(capabilities, (str, bytes)):
            raise ValueError("capabilities must be a list")
        return cls(
            supported_transports=frozenset(map(str, transports)),
            capabilities=frozenset(map(str, capabilities)),
        )


@dataclass(frozen=True)
class ControlCase:
    local_control_case_id: str
    upstream_case_id: str
    category: str
    transport: str
    requires: tuple[str, ...]
    expected_verdict: str
    payload: Mapping[str, Any]
    source_path: str
    corpus_root: Path


def enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(FEATURE_FLAG, "").strip().lower() in {"1", "true", "yes"}


def load_cases(root: Path) -> tuple[ControlCase, ...]:
    """Resolve each logical case exactly once in stable ID order."""
    actual_digest = corpus_sha256(root)
    if actual_digest != CASES_SHA256:
        raise ValueError(
            f"pinned corpus digest mismatch: expected {CASES_SHA256}, got {actual_digest}"
        )
    cases: list[ControlCase] = []
    paths = sorted(root.glob("*/*.json"))
    paths.extend(sorted(root.glob("mcp-drift/*/case.yaml")))
    for path in paths:
        raw = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.suffix == ".json"
            else yaml.safe_load(path.read_text(encoding="utf-8"))
        )
        upstream_id = str(raw["id"])
        cases.append(
            ControlCase(
                local_control_case_id=f"oss.agent-egress.{upstream_id}",
                upstream_case_id=upstream_id,
                category=str(raw["category"]),
                transport=str(raw["transport"]),
                requires=tuple(map(str, raw.get("requires", ()))),
                expected_verdict=str(raw["expected_verdict"]),
                payload=raw,
                source_path=path.relative_to(root).as_posix(),
                corpus_root=root.resolve(),
            )
        )
    ids = [case.upstream_case_id for case in cases]
    if len(ids) != 197 or len(set(ids)) != 197:
        raise ValueError(f"pinned corpus must resolve to 197 unique cases, got {len(ids)}")
    if len({case.category for case in cases}) != 18:
        raise ValueError("pinned corpus must resolve to 18 categories")
    return tuple(sorted(cases, key=lambda case: case.upstream_case_id))


def applicability(case: ControlCase, inputs: ApplicabilityInputs) -> bool:
    """Deterministic AND semantics for transport and declared requirements."""
    return (
        case.transport in inputs.supported_transports
        and set(case.requires).issubset(inputs.capabilities)
    )


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _resolve_evidence(evidence_uri: str, claimed_sha256: str) -> tuple[str, bytes]:
    """Bind a receipt to readable local evidence bytes before any evaluation."""
    if not isinstance(claimed_sha256, str) or not _SHA256_RE.fullmatch(claimed_sha256):
        raise ReceiptBoundaryError("evidence_integrity_invalid", "evidence_sha256 must match canonical sha256:<64-lowercase-hex>")
    parsed = urlparse(evidence_uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ReceiptBoundaryError("evidence_location_invalid", "evidence_uri must resolve to a local file")
    if parsed.query or parsed.fragment:
        raise ReceiptBoundaryError("evidence_location_invalid", "file evidence_uri must not contain query or fragment components")
    path = Path(unquote(parsed.path))
    if not path.is_file():
        raise ReceiptBoundaryError("evidence_location_invalid", "evidence_uri must resolve to a readable regular file")
    try:
        evidence_bytes = path.read_bytes()
        actual = f"sha256:{hashlib.sha256(evidence_bytes).hexdigest()}"
    except OSError as exc:
        raise ReceiptBoundaryError("evidence_location_invalid", "evidence_uri must resolve to a readable regular file") from exc
    if actual != claimed_sha256:
        raise ReceiptBoundaryError("evidence_integrity_mismatch", "evidence_sha256 content mismatch")
    return actual, evidence_bytes


def _load_pinned_refs(corpus_root: Path, case_refs: Sequence[str]) -> tuple[ControlCase, ...]:
    try:
        pinned = load_cases(corpus_root)
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ReceiptBoundaryError("corpus_invalid", "pinned corpus verification failed") from exc
    by_id = {case.upstream_case_id: case for case in pinned}
    by_path = {case.source_path: case for case in pinned}
    if not case_refs or any(type(ref) is not str or not ref for ref in case_refs):
        raise ReceiptBoundaryError("case_ref_invalid", "case references must be nonempty strings")
    resolved: list[ControlCase] = []
    seen: set[str] = set()
    for ref in case_refs:
        case = by_id.get(ref) or by_path.get(ref)
        if case is None:
            raise ReceiptBoundaryError("case_ref_unknown", f"unknown pinned case reference: {ref}")
        if case.upstream_case_id in seen:
            raise ReceiptBoundaryError("case_ref_duplicate", f"duplicate pinned case reference: {ref}")
        seen.add(case.upstream_case_id)
        resolved.append(case)
    return tuple(resolved)


def _parse_observations(evidence_bytes: bytes, expected_ids: set[str]) -> dict[str, tuple[str, str | None]]:
    try:
        document = json.loads(evidence_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptBoundaryError("evidence_schema_invalid", "evidence must be UTF-8 JSON") from exc
    if type(document) is not dict or set(document) != {"schema_version", "observations"}:
        raise ReceiptBoundaryError("evidence_schema_invalid", "evidence top-level fields are closed")
    if document["schema_version"] != EVIDENCE_SCHEMA_VERSION or type(document["observations"]) is not list:
        raise ReceiptBoundaryError("evidence_schema_invalid", "unsupported evidence schema")
    observations: dict[str, tuple[str, str | None]] = {}
    for row in document["observations"]:
        if type(row) is not dict or not {"case_id", "verdict"}.issubset(row) or not set(row).issubset({"case_id", "verdict", "error_code"}):
            raise ReceiptBoundaryError("evidence_schema_invalid", "observation fields are closed")
        case_id, verdict = row["case_id"], row["verdict"]
        error_code = row.get("error_code")
        if type(case_id) is not str or case_id in observations:
            raise ReceiptBoundaryError("evidence_case_duplicate", "each case must have exactly one observation")
        if case_id not in expected_ids:
            raise ReceiptBoundaryError("evidence_case_unknown", f"unexpected case observation: {case_id}")
        if verdict not in {"block", "allow", "warn", "error"}:
            raise ReceiptBoundaryError("evidence_verdict_invalid", f"invalid verdict for {case_id}")
        if verdict == "error":
            if error_code not in ERROR_CODES:
                raise ReceiptBoundaryError("evidence_error_code_invalid", f"invalid error code for {case_id}")
        elif error_code is not None:
            raise ReceiptBoundaryError("evidence_error_code_invalid", "error_code requires error verdict")
        observations[case_id] = (verdict, error_code)
    missing = expected_ids - observations.keys()
    if missing:
        raise ReceiptBoundaryError("evidence_case_missing", f"missing observations: {','.join(sorted(missing))}")
    return observations


def _validate_signing_identity(signing_key: Ed25519PrivateKey, signing_key_id: str, trusted_public_keys: Mapping[str, Ed25519PublicKey]) -> None:
    if type(signing_key_id) is not str or not signing_key_id.strip():
        raise ReceiptBoundaryError("signing_key_id_invalid", "signing key ID must be canonical and nonempty")
    if signing_key_id != signing_key_id.strip() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", signing_key_id):
        raise ReceiptBoundaryError("signing_key_id_invalid", "signing key ID is not canonical")
    trusted = trusted_public_keys.get(signing_key_id)
    if not isinstance(trusted, Ed25519PublicKey):
        raise ReceiptBoundaryError("signing_key_untrusted", "signing key ID is absent from trust mapping")
    supplied = signing_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    expected = trusted.public_bytes(Encoding.Raw, PublicFormat.Raw)
    if supplied != expected:
        raise ReceiptBoundaryError("signing_key_mismatch", "private key does not match trusted public identity")


def run(
    case_refs: Sequence[str],
    inputs: ApplicabilityInputs,
    *,
    corpus_root: Path,
    evidence_uri: str,
    evidence_sha256: str,
    signing_key: Ed25519PrivateKey,
    signing_key_id: str,
    trusted_public_keys: Mapping[str, Ed25519PublicKey],
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not enabled(env):
        raise RuntimeError(f"{FEATURE_FLAG} is disabled")
    cases = _load_pinned_refs(corpus_root, case_refs)
    _validate_signing_identity(signing_key, signing_key_id, trusted_public_keys)
    verified_evidence_sha256, evidence_bytes = _resolve_evidence(evidence_uri, evidence_sha256)
    applicable_ids = {case.upstream_case_id for case in cases if applicability(case, inputs)}
    observations = _parse_observations(evidence_bytes, applicable_ids)
    results: list[dict[str, str]] = []
    for case in cases:
        applies = applicability(case, inputs)
        if not applies:
            outcome = "not_applicable"
            actual = "not_applicable"
        else:
            actual, error_code = observations[case.upstream_case_id]
            outcome = "errored" if actual == "error" else ("passed" if actual == case.expected_verdict else "failed")
        results.append(
            {
                "local_control_case_id": case.local_control_case_id,
                "source_case_id": case.upstream_case_id,
                "source_path": case.source_path,
                "case_class": "customer_control",
                "applicability_predicate": "eligible_egress_control_v1",
                "applicability_result": "applicable" if applies else "not_applicable",
                "expected_verdict": case.expected_verdict,
                "actual_verdict": actual,
                "outcome": outcome,
                "oracle_version": ORACLE_VERSION,
                "execution_seed": "deterministic-no-randomness",
                "attempt": "1",
                "evidence_uri": evidence_uri,
                **({"error_code": error_code} if applies and error_code is not None else {}),
            }
        )
    counts = Counter(result["outcome"] for result in results)
    summary = {name: counts[name] for name in OUTCOMES}
    summary["applicable"] = summary["passed"] + summary["failed"] + summary["errored"]
    role_counts = {
        "target_calls": 0, "simulator_calls": 0, "attacker_calls": 0, "judge_calls": 0,
    }
    if any(value != 0 for value in role_counts.values()):
        raise RuntimeError("model-role call observed inside local execution boundary")
    receipt: dict[str, Any] = {
        "version": RECEIPT_VERSION,
        "namespace": "customer_controls.agent_egress",
        "source_project": SOURCE_PROJECT,
        "source_commit": SOURCE_COMMIT,
        "source_cases_tree": CASES_TREE,
        "oracle_version": ORACLE_VERSION,
        "adapter_version": "agent-egress-control-adapter-v2",
        "case_class": "customer_control",
        "license_ref": "third_party/oss-pilots/agent-egress-bench/LICENSE",
        "execution_replicates": 1,
        "evidence_uri": evidence_uri,
        "evidence_sha256": verified_evidence_sha256,
        "counts": summary,
        "results": results,
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "execution_boundary": "authenticated-semantic-evidence-v1",
        "telemetry_verified": True,
        "telemetry": role_counts,
        "signing_key_id": signing_key_id,
        "signature_algorithm": SIGNATURE_ALGORITHM,
    }
    receipt["signature"] = base64.b64encode(
        signing_key.sign(_canonical_bytes(receipt))
    ).decode("ascii")
    return receipt


def verify_receipt(receipt: Mapping[str, Any], public_key: Ed25519PublicKey) -> bool:
    try:
        signature = base64.b64decode(str(receipt["signature"]), validate=True)
        unsigned = dict(receipt)
        del unsigned["signature"]
        public_key.verify(signature, _canonical_bytes(unsigned))
        return True
    except (InvalidSignature, KeyError, TypeError, ValueError):
        return False


def corpus_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"
