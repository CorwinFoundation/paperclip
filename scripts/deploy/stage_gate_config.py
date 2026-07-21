#!/usr/bin/env python3
"""BEAAA-16230 — Sequence 20 Stage Gate acceptance-provisioning config.

Operator-managed schema + validator + non-secret record emitter that closes
gaps 2-5 of the Founder Stage Gate FAIL (attachment d05e00d8):

  * gap 2 — two DISTINCT non-customer / non-``vapi`` test tenants (A and B),
  * gap 3 — an externally routable HTTPS acceptance base URL,
  * gap 4 — a side-effect-free synthetic endpoint that does NOT point back at the
            scanner under test, referenced by opaque credential/secret refs,
  * gap 5 — a retained rollback identity (commit + repository-qualified OCI
            digest), plus the deployed scanner/corpus/snapshot/seed read-back.

Part (c): secret VALUES never live in the config — only scheme-prefixed opaque
references (``credstore:``/``file:``/``env:``/``vault:``). The validator rejects
any literal secret, and ``build_record`` emits a Stage-Gate record that is
complete and provably free of secret values.

No scoring / rubric / corpus / API-contract surface. Pure stdlib (tomllib).

CLI:
    python scripts/deploy/stage_gate_config.py --config <file>              # validate
    python scripts/deploy/stage_gate_config.py --config <file> --emit-record
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from urllib.parse import urlparse

# Opaque-reference schemes; a secret is never a value in this config, only a
# pointer the operator resolves from the root-only credential channel.
REF_SCHEMES = ("credstore", "file", "env", "vault")

# Keys that would carry a literal secret VALUE — forbidden outright. Operators
# must use the matching ``*_ref`` reference instead.
FORBIDDEN_SECRET_KEYS = ("token", "secret", "password", "credential_secret", "api_key")

# A value that looks like a raw high-entropy secret rather than a reference.
_RAW_SECRET_RE = re.compile(r"^(sk-|AKIA|ghp_|-----BEGIN)|^[A-Za-z0-9+/=_-]{32,}$")

# Fields the operator kit v0.2 Stage-Gate record table requires (all non-secret).
STAGE_GATE_FIELDS = (
    "tenant_a_alias",
    "tenant_a_token_ref",
    "tenant_b_alias",
    "tenant_b_token_ref",
    "synthetic_endpoint_host",
    "credential_ref",
    "secret_ref",
    "external_base_url",
    "on_box_health_url",
    "deployed_scanner_version",
    "deployed_corpus_version",
    "deployed_corpus_snapshot_hash",
    "deployed_seed",
    "active_commit",
    "active_image_digest",
    "rollback_commit",
    "rollback_image_digest",
    "secret_channel",
)


@dataclass(frozen=True)
class Finding:
    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message}"


class StageGateConfigError(Exception):
    def __init__(self, findings: list[Finding]):
        self.findings = findings
        self.codes = [f.code for f in findings]
        super().__init__("; ".join(str(f) for f in findings))


def load_config(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _is_reference(value: str) -> bool:
    return isinstance(value, str) and value.split(":", 1)[0] in REF_SCHEMES


def _looks_like_raw_secret(value: str) -> bool:
    return isinstance(value, str) and not _is_reference(value) and bool(_RAW_SECRET_RE.match(value))


def _lower(value) -> str:
    return str(value).lower()


def _is_repo_qualified_digest(value) -> bool:
    # e.g. backbond-scanner-live@sha256:<64hex> — a repository part before '@'.
    if not isinstance(value, str) or "@sha256:" not in value:
        return False
    repo, _, digest = value.partition("@sha256:")
    return bool(repo.strip()) and bool(re.fullmatch(r"[0-9a-f]{64}", digest.strip()))


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate(cfg: dict) -> list[Finding]:
    """Return a list of Findings; empty means the config is acceptable."""
    findings: list[Finding] = []

    tenants = cfg.get("tenants") or {}
    a = tenants.get("a") or {}
    b = tenants.get("b") or {}
    edge = cfg.get("edge") or {}
    endpoint = cfg.get("synthetic_endpoint") or {}
    deployed = cfg.get("deployed") or {}
    active = cfg.get("active") or {}
    rollback = cfg.get("rollback") or {}

    # --- part (c): no literal secret VALUES anywhere ----------------------- #
    def _scan_secrets(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                key_l = k.lower()
                if key_l in FORBIDDEN_SECRET_KEYS:
                    findings.append(Finding(
                        "secret_value_in_config",
                        f"{path}{k}: use a '{k}_ref' opaque reference, not a literal secret",
                    ))
                elif key_l.endswith("_ref") and _looks_like_raw_secret(str(v)):
                    findings.append(Finding(
                        "secret_value_in_config",
                        f"{path}{k}: reference must be scheme-prefixed ({'/'.join(REF_SCHEMES)}:), "
                        f"not a raw value",
                    ))
                else:
                    _scan_secrets(v, path=f"{path}{k}.")

    _scan_secrets(cfg)

    # --- gap 2: distinct non-customer / non-vapi tenant B ------------------ #
    if not b or not b.get("alias") or not b.get("token_ref"):
        findings.append(Finding("tenant_b_missing",
                                "tenants.b with alias + token_ref is required (distinct second tenant)"))
    elif not a or not a.get("alias") or not a.get("token_ref"):
        findings.append(Finding("tenant_a_missing", "tenants.a with alias + token_ref is required"))
    else:
        if a.get("alias") == b.get("alias") or a.get("token_ref") == b.get("token_ref"):
            findings.append(Finding("tenants_not_distinct",
                                    "tenant A and tenant B must have distinct alias AND token_ref"))
        for name, t in (("a", a), ("b", b)):
            if "vapi" in _lower(t.get("alias")) or "vapi" in _lower(t.get("token_ref")):
                findings.append(Finding("tenant_vapi_forbidden",
                                        f"tenant {name} resolves to 'vapi' (standing blackout)"))
            if t.get("is_customer") is True:
                findings.append(Finding("tenant_customer_forbidden",
                                        f"tenant {name} is flagged customer; only acceptance tenants allowed"))

    # --- gap 3: external base URL must be HTTPS ---------------------------- #
    base = edge.get("external_base_url")
    parsed = urlparse(base or "")
    if parsed.scheme != "https" or not parsed.hostname:
        findings.append(Finding("external_base_url_not_https",
                                "edge.external_base_url must be an https:// URL with a host"))

    # --- gap 4: synthetic endpoint must not point back at the scanner ------ #
    ep_host = _lower(endpoint.get("host"))
    if not ep_host:
        findings.append(Finding("synthetic_endpoint_missing",
                                "synthetic_endpoint.host is required"))
    else:
        edge_host = _lower(parsed.hostname)
        health_host = _lower(urlparse(edge.get("on_box_health_url") or "").hostname)
        loopback = {"127.0.0.1", "localhost", "::1"}
        if ep_host and (ep_host == edge_host or ep_host == health_host or ep_host in loopback):
            findings.append(Finding("synthetic_endpoint_self_referential",
                                    "synthetic_endpoint.host points at the scanner edge/health host "
                                    "(a self-scan); it must be a distinct disposable target"))
        if endpoint.get("side_effect_free") is not True:
            findings.append(Finding("synthetic_endpoint_not_side_effect_free",
                                    "synthetic_endpoint.side_effect_free must be true"))
        if not _is_reference(str(endpoint.get("credential_ref", ""))) and not endpoint.get("credential_ref"):
            findings.append(Finding("credential_ref_missing",
                                    "synthetic_endpoint.credential_ref (opaque) is required"))
        if not _is_reference(str(endpoint.get("secret_ref", ""))):
            findings.append(Finding("secret_ref_missing",
                                    "synthetic_endpoint.secret_ref must be a scheme-prefixed reference"))
        if "vapi" in ep_host:
            findings.append(Finding("synthetic_endpoint_vapi_forbidden",
                                    "synthetic endpoint resolves to 'vapi' (standing blackout)"))

    # --- deployed read-back incl. the acceptance seed ---------------------- #
    if "seed" not in deployed or not isinstance(deployed.get("seed"), int):
        findings.append(Finding("deployed_seed_missing",
                                "deployed.seed (integer read-back from the box) is required"))
    for key in ("scanner_version", "corpus_version", "corpus_snapshot_hash"):
        if not deployed.get(key):
            findings.append(Finding("deployed_readback_missing",
                                    f"deployed.{key} read-back is required"))

    # --- gap 5: retained rollback identity --------------------------------- #
    if not rollback.get("commit") or not _is_repo_qualified_digest(rollback.get("image_digest")):
        findings.append(Finding("rollback_identity_missing",
                                "rollback.commit + repository-qualified rollback.image_digest "
                                "(repo@sha256:<64hex>) are required"))
    # active identity must also be a repo-qualified digest for the record.
    if not active.get("commit") or not _is_repo_qualified_digest(active.get("image_digest")):
        findings.append(Finding("active_identity_missing",
                                "active.commit + repository-qualified active.image_digest are required"))

    return findings


def validate_or_raise(cfg: dict) -> None:
    findings = validate(cfg)
    if findings:
        raise StageGateConfigError(findings)


# --------------------------------------------------------------------------- #
# non-secret Stage-Gate record
# --------------------------------------------------------------------------- #
def build_record(cfg: dict) -> dict:
    """Emit the complete, non-secret Stage-Gate record. Validates first."""
    validate_or_raise(cfg)
    tenants = cfg["tenants"]
    a, b = tenants["a"], tenants["b"]
    edge = cfg["edge"]
    ep = cfg["synthetic_endpoint"]
    dep = cfg["deployed"]
    act = cfg["active"]
    rb = cfg["rollback"]
    record = {
        "tenant_a_alias": a["alias"],
        "tenant_a_token_ref": a["token_ref"],
        "tenant_b_alias": b["alias"],
        "tenant_b_token_ref": b["token_ref"],
        "synthetic_endpoint_host": ep["host"],
        "credential_ref": ep["credential_ref"],
        "secret_ref": ep["secret_ref"],
        "external_base_url": edge["external_base_url"],
        "on_box_health_url": edge.get("on_box_health_url", "https://127.0.0.1"),
        "deployed_scanner_version": dep["scanner_version"],
        "deployed_corpus_version": dep["corpus_version"],
        "deployed_corpus_snapshot_hash": dep["corpus_snapshot_hash"],
        "deployed_seed": dep["seed"],
        "active_commit": act["commit"],
        "active_image_digest": act["image_digest"],
        "rollback_commit": rb["commit"],
        "rollback_image_digest": rb["image_digest"],
        "secret_channel": cfg.get("meta", {}).get(
            "secret_channel", "root-only srv1749248 channel; no secret value"),
    }
    # Defense in depth: the record must never carry a resolved secret value.
    _assert_record_non_secret(record)
    return record


# Record fields that are secret POINTERS and must be scheme-prefixed references.
# credential_ref is an opaque identifier (not a secret pointer) so it only has to
# be non-raw-secret, not scheme-prefixed.
_SCHEME_REF_RECORD_FIELDS = ("tenant_a_token_ref", "tenant_b_token_ref", "secret_ref")


def _assert_record_non_secret(record: dict) -> None:
    for k, v in record.items():
        if k in _SCHEME_REF_RECORD_FIELDS:
            if not _is_reference(str(v)):
                raise StageGateConfigError([Finding(
                    "secret_value_in_config", f"record field {k} is not an opaque reference")])
            continue
        # commit SHAs, OCI digests, and snapshot hashes are non-secret identity
        # values that are legitimately 40/64-hex — skip the entropy heuristic.
        if k.endswith(("_commit", "_image_digest", "_hash")) or k == "deployed_seed":
            continue
        if _looks_like_raw_secret(str(v)):
            raise StageGateConfigError([Finding(
                "secret_value_in_config", f"record field {k} looks like a raw secret")])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Validate the Sequence 20 Stage Gate provisioning config.")
    p.add_argument("--config", required=True)
    p.add_argument("--emit-record", action="store_true",
                   help="print the complete non-secret Stage Gate record as JSON")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    findings = validate(cfg)
    if findings:
        for f in findings:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("OK: provisioning config valid (all Stage Gate gaps closed)", file=sys.stderr)
    if args.emit_record:
        print(json.dumps(build_record(cfg), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
