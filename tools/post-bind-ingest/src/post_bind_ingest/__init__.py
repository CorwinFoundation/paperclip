"""Post-bind ingest package."""

from .manifest_signature import ManifestSchemaError, ManifestSignatureError, validate_manifest_schema
from .egress import (
    EgressConfig,
    EgressGateError,
    EgressResult,
    build_monitor_artifact,
    transmit_monitor_artifact,
    validate_monitor_artifact,
)
from .normalizer import NormalizerError, normalize_manifest, normalize_receiver_events
from .invariants import ManifestIdempotencyStore
from .continuity_gate import (
    ContinuityGateDecision,
    ContinuityGateError,
    TelemetryGapState,
    classify_continuity_status,
    compute_gap_state,
    evaluate_attach_renew_continuity,
)
from .detector_input import normalize_tool_name
from .p5_detector import detect_p5_events
from .receiver import (
    ReceiverEnvelope,
    ReceiverError,
    ReceiverReplayError,
    ReceiverSchemaError,
    ReceiverSignatureError,
    RedactionConformanceError,
    parse_receiver_payload,
)

__all__ = [
    "ManifestSchemaError",
    "ManifestSignatureError",
    "ManifestIdempotencyStore",
    "EgressConfig",
    "EgressGateError",
    "EgressResult",
    "ReceiverEnvelope",
    "ReceiverError",
    "ReceiverReplayError",
    "ReceiverSchemaError",
    "ReceiverSignatureError",
    "RedactionConformanceError",
    "NormalizerError",
    "ContinuityGateDecision",
    "ContinuityGateError",
    "TelemetryGapState",
    "classify_continuity_status",
    "compute_gap_state",
    "evaluate_attach_renew_continuity",
    "normalize_tool_name",
    "normalize_receiver_events",
    "validate_manifest_schema",
    "normalize_manifest",
    "detect_p5_events",
    "parse_receiver_payload",
    "build_monitor_artifact",
    "transmit_monitor_artifact",
    "validate_monitor_artifact",
]
