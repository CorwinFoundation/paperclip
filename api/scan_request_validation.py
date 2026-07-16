"""Internal request-validation seam for the Scanner API."""
from __future__ import annotations

import ipaddress
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from api.config import ApiConfig
from api.handlers import HandlerError
from harness import egress
from scanners.live_probe_orchestrator import ALLOWED_K_REPLICATES, DEFAULT_K_REPLICATES

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)
_CRED_REF_SHAPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-:.]{0,127}$")
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$"
)


def require(body: Mapping[str, Any], field_name: str) -> Any:
    value = body.get(field_name)
    if value is None or value == "":
        raise HandlerError(422, f"{field_name}_required")
    return value


def validate_scan_mode(scan_mode: str) -> None:
    if scan_mode != "live_probe":
        raise HandlerError(422, "scan_mode_invalid")


def enforce_no_residential_proxy_for_live_probe() -> None:
    if egress.is_enabled():
        raise HandlerError(422, "residential_proxy_forbidden_for_live_probe")


def verify_live_egress_fail_closed(*, scanner_version: str, corpus_version: str, seed: int) -> None:
    try:
        egress.verify_live_egress_fail_closed(
            scanner_version=str(scanner_version),
            corpus_version=str(corpus_version),
            seed=seed,
            execution_environment=ApiConfig.from_env().execution_environment,
        )
    except egress.LiveEgressPostureError as exc:
        raise HandlerError(422, "live_egress_default_deny_unverified") from exc


def validate_k_replicates(k: Any) -> int:
    if k is None:
        return DEFAULT_K_REPLICATES
    if isinstance(k, bool) or not isinstance(k, int) or k not in ALLOWED_K_REPLICATES:
        raise HandlerError(422, "k_replicates_invalid")
    return k


def validate_credential_ref(credential_ref: Any) -> str:
    if not isinstance(credential_ref, str) or not _CRED_REF_SHAPE_RE.match(credential_ref):
        raise HandlerError(422, "credential_ref_invalid")
    return credential_ref


def validate_corpus_snapshot_hash(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise HandlerError(422, "corpus_snapshot_hash_invalid")
    return value


def _is_private_only_literal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_unspecified
        and not ip.is_reserved
    )


def validate_https_url_or_host_safety(
    value: str,
    *,
    error_code: str,
    allow_private_ip_literals: bool = False,
    require_https_url: bool = False,
) -> str:
    """Validate a URL/host literal without doing DNS resolution.

    The egress layer remains authoritative for resolve-time checks. Private
    literals may be allowed for agent endpoints in the supported vendor-VPC
    deployment model, while loopback, link-local, metadata, and other
    forbidden literal ranges fail at request intake.
    """
    host = value
    if "://" in value or require_https_url:
        try:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname:
                raise HandlerError(422, error_code)
            if parsed.username is not None or parsed.password is not None:
                raise HandlerError(422, error_code)
            host = parsed.hostname
        except ValueError as exc:
            raise HandlerError(422, error_code) from exc

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host

    if allow_private_ip_literals and _is_private_only_literal(ip):
        return host
    if egress._is_forbidden_resolved_ip(str(ip)):
        raise HandlerError(422, error_code)
    return host


def validate_agent_endpoint(agent_endpoint: Any) -> dict[str, str]:
    if not isinstance(agent_endpoint, dict):
        raise HandlerError(422, "agent_endpoint_invalid")
    host = agent_endpoint.get("host")
    if not isinstance(host, str) or not _HOSTNAME_RE.match(host):
        raise HandlerError(422, "agent_endpoint_host_invalid")
    validate_https_url_or_host_safety(
        host,
        error_code="agent_endpoint_host_forbidden",
        allow_private_ip_literals=True,
    )
    return {"host": host, "transport": str(agent_endpoint.get("transport", "rest"))}
