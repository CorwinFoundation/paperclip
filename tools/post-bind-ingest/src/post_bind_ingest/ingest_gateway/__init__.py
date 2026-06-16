"""Push-mode signed-manifest ingest gateway.

ADR-0034 §2 wire shape, MVP per CTO brief §4 (local-FS S3 substrate,
HMAC-SHA256 signature, raw_stdout adapter minimum). Production S3 / KMS / IAM
wiring is Phase 2.0.1.
"""

from .manifest_signature import (
    ManifestSignatureError,
    sign_manifest_body,
    verify_manifest_signature,
)
from .manifest_schema import ManifestSchemaError, validate_manifest
from .idempotency import IdempotencyStore
from .dead_letter import DeadLetterQueue
from .manifest_receiver import (
    GatewayResult,
    ManifestReceiver,
    SUPPORTED_SCHEMA_VERSIONS,
)

__all__ = [
    "ManifestSignatureError",
    "sign_manifest_body",
    "verify_manifest_signature",
    "ManifestSchemaError",
    "validate_manifest",
    "IdempotencyStore",
    "DeadLetterQueue",
    "GatewayResult",
    "ManifestReceiver",
    "SUPPORTED_SCHEMA_VERSIONS",
]
