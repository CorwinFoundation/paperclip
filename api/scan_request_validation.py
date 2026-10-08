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


def validate_agent_endpoint(agent_endpoint: Any) -> dict[str, Any]:
    if not isinstance(agent_endpoint, dict):
        raise HandlerError(422, "agent_endpoint_invalid")
    raw_url = agent_endpoint.get("url")
    host = agent_endpoint.get("host")
    if raw_url is not None:
        if not isinstance(raw_url, str):
            raise HandlerError(422, "agent_endpoint_url_invalid")
        parsed = urlsplit(raw_url)
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.fragment
            or parsed.username is not None or parsed.password is not None
        ):
            raise HandlerError(422, "agent_endpoint_url_invalid")
        endpoint_url = raw_url
        endpoint_host = parsed.hostname
    else:
        if not isinstance(host, str) or not _HOSTNAME_RE.match(host):
            raise HandlerError(422, "agent_endpoint_host_invalid")
        endpoint_url = f"https://{host}"
        endpoint_host = host
    validate_https_url_or_host_safety(
        endpoint_url,
        error_code="agent_endpoint_host_forbidden",
        allow_private_ip_literals=True,
    )
    transport = agent_endpoint.get("transport", "rest")
    if transport not in {"rest", "openai_chat", "json_map", "kaltura_genie", "kaltura_genie_browser"}:
        raise HandlerError(422, "agent_endpoint_transport_invalid")
    if raw_url is None and set(agent_endpoint) <= {"host", "transport"}:
        return {"host": endpoint_host, "transport": transport}
    auth = agent_endpoint.get("auth", {"type": "bearer"})
    if not isinstance(auth, dict) or auth.get("type", "bearer") not in {
        "bearer", "custom_header", "none",
    }:
        raise HandlerError(422, "agent_endpoint_auth_invalid")
    if auth.get("type") == "custom_header":
        header_name = auth.get("header_name")
        if not isinstance(header_name, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", header_name):
            raise HandlerError(422, "agent_endpoint_auth_header_invalid")
    config: dict[str, Any] = {
        "url": endpoint_url,
        "host": endpoint_host,
        "transport": transport,
        "auth": dict(auth),
    }
    for key in ("model", "system_prompt", "request_template", "response_path"):
        if key in agent_endpoint:
            config[key] = agent_endpoint[key]
    if transport == "openai_chat" and not isinstance(config.get("model"), str):
        raise HandlerError(422, "agent_endpoint_model_required")
    if transport == "json_map":
        if not isinstance(config.get("request_template"), dict):
            raise HandlerError(422, "agent_endpoint_request_template_required")
        if _prompt_placeholder_count(config["request_template"]) != 1:
            raise HandlerError(422, "agent_endpoint_prompt_placeholder_invalid")
        if not isinstance(config.get("response_path"), str):
            raise HandlerError(422, "agent_endpoint_response_path_required")
    if transport == "kaltura_genie":
        if not re.fullmatch(r"genie\.[a-z0-9-]+\.ovp\.kaltura\.com", endpoint_host):
            raise HandlerError(422, "kaltura_genie_host_invalid")
        if urlsplit(endpoint_url).path.rstrip("/") != "/assistant/converse":
            raise HandlerError(422, "kaltura_genie_path_invalid")
    if transport == "kaltura_genie_browser":
        if endpoint_url != "https://learning.kaltura.com/":
            raise HandlerError(422, "kaltura_genie_browser_url_invalid")
        if auth.get("type") != "none":
            raise HandlerError(422, "kaltura_genie_browser_auth_invalid")
    return config


def _prompt_placeholder_count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_prompt_placeholder_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_prompt_placeholder_count(item) for item in value)
    return int(value == "{{prompt}}")
