"""Fail-closed validation for the C10 exact-set activation manifest.

The QA candidate is intentionally unsigned.  It may be inspected and tested,
but it cannot satisfy the production boundary until an out-of-band Ed25519
authority key verifies the exact canonical payload.
"""
from __future__ import annotations

import base64
import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


SCHEMA = "backbond.c10-activation-manifest/v1"
SIGNATURE_ALGORITHM = "ed25519"
PENDING_SIGNATURE_STATE = "pending_qa"
SIGNED_SIGNATURE_STATE = "production_authority_signed"
AUTHORIZED_FAMILIES = (
    "ai_disclosure",
    "cbrn_refusal",
    "cyber_misuse_refusal",
    "harmful_output_golden",
    "insecure_code_generation",
    "out_of_scope_containment",
    "p3_5_multiturn_jailbreak",
    "p3_6_output_attack",
)


class C10ActivationManifestError(RuntimeError):
    """Raised when C10 activation evidence is incomplete or invalid."""


def sha256_file(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_qa_manifest(*, roster_path: Path | str, ledger_path: Path | str) -> dict[str, Any]:
    roster = json.loads(Path(roster_path).read_text(encoding="utf-8"))
    ledger = [json.loads(line) for line in Path(ledger_path).read_text(encoding="utf-8").splitlines() if line]
    roster_ids = [row["stable_case_id"] for row in roster]
    ledger_ids = [row["stable_case_id"] for row in ledger]
    families = sorted({row["family_id"] for row in roster})
    if len(roster_ids) != 172 or roster_ids != ledger_ids or len(set(roster_ids)) != 172:
        raise C10ActivationManifestError("C10 immutable inputs do not bind the exact 172-row identity set")
    if tuple(families) != AUTHORIZED_FAMILIES:
        raise C10ActivationManifestError("C10 immutable inputs contain an unauthorized family")
    return {
        "schema": SCHEMA,
        "activation_issue": "BEAAA-26259",
        "candidate_base_sha": "c50c2762cfba3ca5066aa34b8d8fa3e75e4b3ef1",
        "rollback_sha": "7a25659b2b8ab1acee055074f2560105470c92d0",
        "scanner_version": "0.5.0",
        "corpus_version": "0.2.0",
        "probe_set_version": "C10-exact-172-v1",
        "seeds": [0, 1, 2, 3, 4],
        "families": families,
        "stable_case_ids": roster_ids,
        "immutable_inputs": {
            "evidence_capsule_id": "03ab4484afbfdb3acb7d987761f44404fb79c80f18171b3792ba7e73c2cddd35",
            "founder_authorized_proposal_sha256": "7b287fb1468296cf5e70c92b37f95f04ada3093211207cf692eb9a6b88778f46",
            "roster_sha256": sha256_file(roster_path),
            "mapping_ledger_sha256": sha256_file(ledger_path),
            "evidence_aggregate_sha256": "5d77c48c28e4fa0319e287802e0c184ca76e30b91edbe92a7b7d1202c71e5bd7",
        },
        "rubric_point_ids": sorted({row["rubric_point_id"] for row in ledger}),
        "score_weights_unchanged_zero": True,
        "signature_state": PENDING_SIGNATURE_STATE,
        "production_activation_allowed": False,
        "signature": None,
    }


def canonical_payload_bytes(manifest: Mapping[str, Any]) -> bytes:
    """Return the signature preimage, excluding only the detached signature."""
    payload = {key: value for key, value in manifest.items() if key != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_manifest(path: Path | str) -> dict[str, Any]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise C10ActivationManifestError(f"C10 activation manifest unavailable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise C10ActivationManifestError("C10 activation manifest root must be an object")
    return manifest


def validate_qa_candidate(manifest: Mapping[str, Any]) -> None:
    """Accept only an explicitly non-production, unsigned QA candidate."""
    if manifest.get("schema") != SCHEMA:
        raise C10ActivationManifestError("C10 activation manifest schema mismatch")
    if manifest.get("signature_state") != PENDING_SIGNATURE_STATE:
        raise C10ActivationManifestError("QA candidate must have pending_qa signature state")
    if manifest.get("production_activation_allowed") is not False:
        raise C10ActivationManifestError("unsigned QA candidate must forbid production activation")
    if manifest.get("signature") not in (None, {}):
        raise C10ActivationManifestError("unsigned QA candidate must not carry a signature")


def verify_production_activation(
    manifest: Mapping[str, Any],
    *,
    authority_public_key: bytes,
    expected_key_id: str,
) -> None:
    """Verify production authority over the exact manifest payload.

    The public key and key identity are caller-pinned inputs.  They are never
    trusted from the manifest being verified.
    """
    if manifest.get("schema") != SCHEMA:
        raise C10ActivationManifestError("C10 activation manifest schema mismatch")
    if manifest.get("signature_state") != SIGNED_SIGNATURE_STATE:
        raise C10ActivationManifestError("C10 production signature is missing")
    if manifest.get("production_activation_allowed") is not True:
        raise C10ActivationManifestError("C10 production activation is not authorized")
    signature = manifest.get("signature")
    if not isinstance(signature, Mapping):
        raise C10ActivationManifestError("C10 production signature object is missing")
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise C10ActivationManifestError("C10 production signature algorithm is invalid")
    if signature.get("key_id") != expected_key_id:
        raise C10ActivationManifestError("C10 production signature key identity is invalid")
    value = signature.get("value")
    if not isinstance(value, str) or not value:
        raise C10ActivationManifestError("C10 production signature value is missing")
    try:
        raw_signature = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        Ed25519PublicKey.from_public_bytes(authority_public_key).verify(
            raw_signature, canonical_payload_bytes(manifest)
        )
    except (ValueError, InvalidSignature) as exc:
        raise C10ActivationManifestError("C10 production signature is invalid") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--roster",
        type=Path,
        default=Path("artifacts/BEAAA-25646/c10-roster-v1.0.json"),
        help="exact 172-row roster JSON",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path("artifacts/BEAAA-25646/c10-mapping-ledger-v1.0.jsonl"),
        help="exact 172-row mapping ledger JSONL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="path for the unsigned QA-only manifest",
    )
    args = parser.parse_args(argv)
    try:
        manifest = build_qa_manifest(roster_path=args.roster, ledger_path=args.ledger)
        validate_qa_candidate(manifest)
    except (C10ActivationManifestError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
