"""Manifest receiver — orchestrates schema validation, signature verification,
idempotency, dead-letter routing, and hands a verified manifest to the
normalizer.

Local-FS S3 substrate per CTO brief §4: manifests live at
`<root>/{customer_id}/manifests/{day_utc}/manifest.json`; log objects live at
`<root>/{customer_id}/raw/{day_utc}/{object_sha256}`. The receiver does not
mutate either tree — it reads the manifest, fetches each referenced object,
and yields a `GatewayResult` describing the outcome.

The receiver also computes the `manifest_sha` (canonical-JSON SHA-256 of the
manifest body, sans signature fields) that downstream normalizer events
embed in their common envelope.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..normalizer.transcript_sha import canonical_json
from .dead_letter import DeadLetterQueue
from .idempotency import IdempotencyStore
from .manifest_schema import ManifestSchemaError, validate_manifest
from .manifest_signature import (
    ManifestSignatureError,
    verify_manifest_signature,
)


SUPPORTED_SCHEMA_VERSIONS = frozenset({"log-event-schema-v1.0"})


@dataclass
class GatewayResult:
    """Outcome of one manifest receive."""

    accepted: bool
    manifest_sha: str | None
    customer_id: str | None
    agent_id: str | None
    day_utc: str | None
    source_kinds: list[str] = field(default_factory=list)
    raw_objects: list[bytes] = field(default_factory=list)
    rejection_reason: str | None = None
    rejection_detail: str | None = None
    replayed: bool = False


@dataclass
class ManifestReceiver:
    """Push-mode manifest receiver. See module docstring."""

    root: Path
    signing_keys: dict[str, bytes]
    idempotency: IdempotencyStore
    dead_letter: DeadLetterQueue

    def receive(self, manifest_body: Any) -> GatewayResult:
        """Process one manifest body, return a GatewayResult.

        On any signature / schema / replay outcome, the result is fully populated;
        callers route accepted results into `normalizer.normalize_stream`.
        """
        # Step 1: schema validation.
        try:
            manifest = validate_manifest(manifest_body)
        except ManifestSchemaError as exc:
            self.dead_letter.write(
                reason="schema_validation_failed",
                detail=str(exc),
                manifest_sha=None,
                manifest_body=manifest_body,
            )
            return GatewayResult(
                accepted=False, manifest_sha=None, customer_id=None,
                agent_id=None, day_utc=None,
                rejection_reason="schema_validation_failed",
                rejection_detail=str(exc),
            )

        customer_id = manifest["customer_id"]
        agent_id = manifest["agent_id"]
        day_utc = manifest["day_utc"]

        # Step 2: signature verification.
        signature_alg = manifest["signature_alg"]
        body_for_sig = {k: v for k, v in manifest.items() if k not in ("signature", "signature_alg")}
        manifest_sha = _compute_manifest_sha(body_for_sig)
        key = self.signing_keys.get(customer_id)
        if key is None:
            self.dead_letter.write(
                reason="signing_key_unknown",
                detail=f"no signing key registered for customer {customer_id}",
                manifest_sha=manifest_sha,
                manifest_body=manifest,
                signature_alg=signature_alg,
            )
            return GatewayResult(
                accepted=False, manifest_sha=manifest_sha,
                customer_id=customer_id, agent_id=agent_id, day_utc=day_utc,
                rejection_reason="signing_key_unknown",
                rejection_detail=f"no signing key registered for customer {customer_id}",
            )
        try:
            verify_manifest_signature(body_for_sig, manifest["signature"],
                                      signature_alg, key)
        except ManifestSignatureError as exc:
            self.dead_letter.write(
                reason="signature_verification_failed",
                detail=str(exc),
                manifest_sha=manifest_sha,
                manifest_body=manifest,
                signature_alg=signature_alg,
            )
            return GatewayResult(
                accepted=False, manifest_sha=manifest_sha,
                customer_id=customer_id, agent_id=agent_id, day_utc=day_utc,
                rejection_reason="signature_verification_failed",
                rejection_detail=str(exc),
            )

        # Step 3: idempotency.
        if self.idempotency.record_and_check(customer_id, manifest_sha):
            return GatewayResult(
                accepted=True, manifest_sha=manifest_sha,
                customer_id=customer_id, agent_id=agent_id, day_utc=day_utc,
                replayed=True,
            )

        # Step 4: fetch log objects from substrate.
        objects: list[bytes] = []
        source_kinds: list[str] = []
        for obj_meta in manifest["log_objects"]:
            obj_sha = obj_meta["sha256"]
            object_path = self.root / customer_id / "raw" / day_utc / obj_sha
            if not object_path.exists():
                self.dead_letter.write(
                    reason="raw_object_missing",
                    detail=f"object {obj_sha} declared in manifest but missing under {object_path}",
                    manifest_sha=manifest_sha,
                    manifest_body=manifest,
                    signature_alg=signature_alg,
                )
                return GatewayResult(
                    accepted=False, manifest_sha=manifest_sha,
                    customer_id=customer_id, agent_id=agent_id, day_utc=day_utc,
                    rejection_reason="raw_object_missing",
                    rejection_detail=f"object {obj_sha} declared in manifest but missing",
                )
            body = object_path.read_bytes()
            observed = "sha256:" + hashlib.sha256(body).hexdigest()
            if observed != obj_sha:
                self.dead_letter.write(
                    reason="raw_object_sha_mismatch",
                    detail=f"declared {obj_sha} but observed {observed}",
                    manifest_sha=manifest_sha,
                    manifest_body=manifest,
                    signature_alg=signature_alg,
                )
                return GatewayResult(
                    accepted=False, manifest_sha=manifest_sha,
                    customer_id=customer_id, agent_id=agent_id, day_utc=day_utc,
                    rejection_reason="raw_object_sha_mismatch",
                    rejection_detail=f"declared {obj_sha} but observed {observed}",
                )
            objects.append(body)
            source_kinds.append(obj_meta["source_kind"])

        return GatewayResult(
            accepted=True, manifest_sha=manifest_sha,
            customer_id=customer_id, agent_id=agent_id, day_utc=day_utc,
            source_kinds=source_kinds, raw_objects=objects,
        )


def _compute_manifest_sha(body_without_signature: dict[str, Any]) -> str:
    canonical = canonical_json(body_without_signature)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
