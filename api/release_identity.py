from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PUBLIC_RELEASE_IDENTITY_FIELDS = (
    "schema_version",
    "environment",
    "host",
    "candidate_id",
    "commit_sha",
    "image_digest",
    "configured_image",
    "running_container_revision",
    "internal_version_sha",
    "current_version",
    "last_activated_sequence",
    "terminal_receipt_sha256",
    "status",
    "recorded_at",
)


class ReleaseIdentityError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def release_identity_path() -> Path:
    return Path(os.environ.get("BACKBOND_RELEASE_IDENTITY_PATH", "/var/lib/backbond-deploy-agent/release_identity.json"))


def read_release_identity(path: Path | None = None) -> dict[str, Any]:
    identity_path = path or release_identity_path()
    if not identity_path.exists():
        raise ReleaseIdentityError("release_identity_not_found")
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReleaseIdentityError("release_identity_invalid_json") from exc
    if not isinstance(identity, dict):
        raise ReleaseIdentityError("release_identity_invalid_shape")
    return {field: identity[field] for field in PUBLIC_RELEASE_IDENTITY_FIELDS if field in identity}
