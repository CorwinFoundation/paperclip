"""File-backed key registry for the license-validation stub (BEAAA-569).

JSON layout (the file is the source of truth for the stub):

    {
      "schema_version": 1,
      "keys": {
        "<license_key>": {
          "partner_id": "...",
          "binary_build_id": "...",
          "issued_at": "ISO-8601",
          "expires_at": "ISO-8601",
          "status": "active" | "revoked",
          "revoked_at": null | "ISO-8601",
          "revocation_reason": null | "..."
        }
      },
      "blacklisted_builds": ["binary_build_id", ...]
    }

The production endpoint replaces this with a transactional store (postgres /
DynamoDB) + a KMS-backed signing key. The schema above is the contract the
stub satisfies; the production swap-in keeps the same record shape so the
admin tooling does not change.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .attestation import _parse_iso

SCHEMA_VERSION = 1
DEFAULT_EXPIRY = datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc)


@dataclass(frozen=True)
class KeyRecord:
    license_key: str
    partner_id: str
    binary_build_id: str
    issued_at: datetime
    expires_at: datetime
    status: str  # "active" | "revoked"
    revoked_at: datetime | None
    revocation_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "partner_id": self.partner_id,
            "binary_build_id": self.binary_build_id,
            "issued_at": _iso(self.issued_at),
            "expires_at": _iso(self.expires_at),
            "status": self.status,
            "revoked_at": _iso(self.revoked_at) if self.revoked_at else None,
            "revocation_reason": self.revocation_reason,
        }


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Registry:
    """Thread-unsafe, process-local file-backed registry.

    Locking is intentionally absent: the stub serves the SE smoke for
    BEAAA-567 and is operated by one process. The production endpoint
    replaces this with a transactional store.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.exists():
            self._write({"schema_version": SCHEMA_VERSION, "keys": {}, "blacklisted_builds": []})

    # ---- low-level I/O ---------------------------------------------------

    def _read(self) -> dict[str, Any]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"registry schema_version {data.get('schema_version')!r} != {SCHEMA_VERSION}"
            )
        return data

    def _write(self, data: dict[str, Any]) -> None:
        # Atomic replace so a crashed write doesn't corrupt the registry.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".registry.", dir=self.path.parent)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, sort_keys=True, indent=2)
                fh.write("\n")
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

    # ---- queries ---------------------------------------------------------

    def get(self, license_key: str) -> KeyRecord | None:
        data = self._read()
        entry = data["keys"].get(license_key)
        if entry is None:
            return None
        return KeyRecord(
            license_key=license_key,
            partner_id=entry["partner_id"],
            binary_build_id=entry["binary_build_id"],
            issued_at=_parse_iso(entry["issued_at"]),
            expires_at=_parse_iso(entry["expires_at"]),
            status=entry["status"],
            revoked_at=_parse_iso(entry["revoked_at"]) if entry.get("revoked_at") else None,
            revocation_reason=entry.get("revocation_reason"),
        )

    def is_build_blacklisted(self, binary_build_id: str) -> bool:
        return binary_build_id in set(self._read().get("blacklisted_builds", []))

    def list_keys(self) -> list[KeyRecord]:
        records: list[KeyRecord] = []
        data = self._read()
        for license_key, entry in data["keys"].items():
            records.append(
                KeyRecord(
                    license_key=license_key,
                    partner_id=entry["partner_id"],
                    binary_build_id=entry["binary_build_id"],
                    issued_at=_parse_iso(entry["issued_at"]),
                    expires_at=_parse_iso(entry["expires_at"]),
                    status=entry["status"],
                    revoked_at=_parse_iso(entry["revoked_at"]) if entry.get("revoked_at") else None,
                    revocation_reason=entry.get("revocation_reason"),
                )
            )
        return records

    # ---- mutations -------------------------------------------------------

    def issue(
        self,
        *,
        partner_id: str,
        binary_build_id: str,
        expires_at: datetime = DEFAULT_EXPIRY,
        license_key: str | None = None,
        now: datetime | None = None,
    ) -> KeyRecord:
        data = self._read()
        key = license_key or _new_license_key()
        if key in data["keys"]:
            raise ValueError(f"license_key {key!r} already exists")
        issued_at = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
        record = KeyRecord(
            license_key=key,
            partner_id=partner_id,
            binary_build_id=binary_build_id,
            issued_at=issued_at,
            expires_at=expires_at,
            status="active",
            revoked_at=None,
            revocation_reason=None,
        )
        data["keys"][key] = record.to_dict()
        self._write(data)
        return record

    def revoke(
        self,
        *,
        license_key: str,
        reason: str,
        now: datetime | None = None,
    ) -> KeyRecord:
        data = self._read()
        entry = data["keys"].get(license_key)
        if entry is None:
            raise KeyError(license_key)
        if entry["status"] == "revoked":
            # idempotent revoke; preserve original revoked_at + reason
            return self.get(license_key)  # type: ignore[return-value]
        moment = (now or datetime.now(tz=timezone.utc)).replace(microsecond=0)
        entry["status"] = "revoked"
        entry["revoked_at"] = _iso(moment)
        entry["revocation_reason"] = reason
        self._write(data)
        return self.get(license_key)  # type: ignore[return-value]

    def blacklist_build(self, binary_build_id: str) -> None:
        data = self._read()
        current = set(data.get("blacklisted_builds", []))
        current.add(binary_build_id)
        data["blacklisted_builds"] = sorted(current)
        self._write(data)


def _new_license_key() -> str:
    return "lk_" + secrets.token_urlsafe(24)
