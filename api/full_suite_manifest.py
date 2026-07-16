"""Compatibility facade for full-suite execution manifests.

BEAAA-14838 extracts models, registry loading, payload construction, integrity,
and persistence without changing existing imports or manifest bytes.
"""
from api.full_suite_manifest_integrity import (
    attach_manifest_signature,
    canonical_manifest_bytes,
    manifest_payload_hash,
    registry_snapshot_hash,
)
from api.full_suite_manifest_models import (
    FULL_SUITE_MANIFEST_VERSION,
    FULL_SUITE_RUNNER_VERSION,
    EvidenceUriResolver,
    FullSuiteCase,
    FullSuiteManifestError,
    ManifestSigner,
)
from api.full_suite_manifest_payload import build_full_suite_manifest
from api.full_suite_manifest_persistence import persist_full_suite_manifest
from api.full_suite_manifest_registry import (
    DEFAULT_ACTIVE_REGISTRY_PATH,
    active_registry_from_canonical_registry,
    active_registry_from_manifest,
    active_registry_from_path,
    active_registry_from_rubric,
)

__all__ = [
    "DEFAULT_ACTIVE_REGISTRY_PATH",
    "FULL_SUITE_MANIFEST_VERSION",
    "FULL_SUITE_RUNNER_VERSION",
    "EvidenceUriResolver",
    "FullSuiteCase",
    "FullSuiteManifestError",
    "ManifestSigner",
    "active_registry_from_manifest",
    "active_registry_from_canonical_registry",
    "active_registry_from_path",
    "active_registry_from_rubric",
    "attach_manifest_signature",
    "build_full_suite_manifest",
    "canonical_manifest_bytes",
    "manifest_payload_hash",
    "persist_full_suite_manifest",
    "registry_snapshot_hash",
]
