from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from . import CONNECTOR_VERSION
from .crypto import EnvelopeSigner, EphemeralKey, b64url_decode, decrypt_task_content, generate_ephemeral_key
from .envelope import build_envelope
from .http import EgressAllowlist, PortalClient, call_agent
from .redaction import RedactionPolicy
from .response_limits import OversizedResponseError, ensure_json_serialized_under_response_cap
from .telemetry import build_redacted_output_record


@dataclass
class ConnectorConfig:
    connector_id: str
    portal_base_url: str
    ca_chain_pem: str
    oidc: dict[str, Any]
    redaction_policy: dict[str, Any]
    portal_version: str
    ephemeral_public_key: str
    counter_signature_public_key: str | None = None
    signing_private_key_pem: str | None = None

    @classmethod
    def load(cls, path: Path) -> "ConnectorConfig":
        return cls(**json.loads(path.read_text()))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))


class ConnectorRuntime:
    def __init__(
        self,
        portal_base_url: str,
        agent_endpoint: str | None,
        config_path: Path,
        ephemeral_key: EphemeralKey | None = None,
        signer: EnvelopeSigner | None = None,
    ):
        self.portal_base_url = portal_base_url
        self.agent_endpoint = agent_endpoint
        self.config_path = config_path
        self.ephemeral_key = ephemeral_key or generate_ephemeral_key()
        self.signer = signer or EnvelopeSigner.generate()
        self.history: list[dict[str, Any]] = []

    def accept_invite(
        self,
        invite_token: str,
        endpoint_url: str,
        cert_ref: str,
        disclosure_consent: bool = False,
    ) -> dict[str, Any]:
        allowlist = EgressAllowlist(self.portal_base_url, self.agent_endpoint)
        client = PortalClient(self.portal_base_url, allowlist)
        return client.accept_invite(
            {
                "invite_token": invite_token,
                "endpoint_url": endpoint_url,
                "cert_ref": cert_ref,
                "endpoint_flag": "staging",
                "disclosure_consent": "1" if disclosure_consent else "",
            }
        )

    def enroll(
        self,
        invite_token: str,
        dp_org_name: str,
        accept_endpoint_url: str | None = None,
        cert_ref: str = "connector-local-cert-ref",
    ) -> ConnectorConfig:
        allowlist = EgressAllowlist(self.portal_base_url, self.agent_endpoint)
        client = PortalClient(self.portal_base_url, allowlist)
        if accept_endpoint_url:
            self.accept_invite(invite_token, accept_endpoint_url, cert_ref)
        payload = {
            "invite_token": invite_token,
            "dp_org_name": dp_org_name,
            "connector_version": CONNECTOR_VERSION,
            "ephemeral_public_key": {"alg": "x25519", "key": self.ephemeral_key.public_key_b64},
            "key_attestation": {"type": self.ephemeral_key.attestation_type, "quote": self.ephemeral_key.quote},
            "signing_public_key_pem": self.signer.public_key_pem(),
        }
        response = client.enroll(payload)
        counter_key = response.get("counter_signature_public_key", {}).get("key")
        RedactionPolicy.from_signed_payload(response["redaction_policy"], response["ca_chain_pem"], counter_key)
        config = ConnectorConfig(
            connector_id=response["connector_id"],
            portal_base_url=self.portal_base_url,
            ca_chain_pem=response["ca_chain_pem"],
            oidc=response["oidc"],
            redaction_policy=response["redaction_policy"],
            portal_version=response["portal_version"],
            ephemeral_public_key=self.ephemeral_key.public_key_b64,
            counter_signature_public_key=counter_key,
            signing_private_key_pem=self.signer.private_key_pem(),
        )
        config.save(self.config_path)
        return config

    def run_scan(self, scan_id: str, max_tasks: int | None = None) -> int:
        if not self.agent_endpoint:
            raise ValueError("DP_AGENT_ENDPOINT is required for scan execution")
        config = ConnectorConfig.load(self.config_path)
        if config.signing_private_key_pem:
            self.signer = EnvelopeSigner.from_private_key_pem(config.signing_private_key_pem)
        policy = RedactionPolicy.from_signed_payload(
            config.redaction_policy,
            config.ca_chain_pem,
            config.counter_signature_public_key,
        )
        token = config.oidc.get("access_token") or config.oidc.get("client_secret")
        allowlist = EgressAllowlist(config.portal_base_url, self.agent_endpoint)
        client = PortalClient(config.portal_base_url, allowlist, bearer_token=token)
        completed = 0

        while max_tasks is None or completed < max_tasks:
            response = client.next_task(scan_id, config.connector_id)
            if response.status_code == 204:
                break
            task = response.json()
            envelope = self._execute_task(scan_id, task, policy, allowlist)
            client.post_envelope(scan_id, envelope)
            self.history.append({"scan_id": scan_id, "task_id": task["task_id"], "posted": True})
            completed += 1

        client.complete(scan_id)
        return completed

    def emit_redacted_record(
        self,
        dp_id: str,
        agent_call_id: str,
        agent_output_text: str,
        capture_window_start: str | None = None,
    ) -> dict[str, Any]:
        config = ConnectorConfig.load(self.config_path)
        if config.signing_private_key_pem:
            self.signer = EnvelopeSigner.from_private_key_pem(config.signing_private_key_pem)
        try:
            policy = RedactionPolicy.from_signed_payload(
                config.redaction_policy,
                config.ca_chain_pem,
                config.counter_signature_public_key,
            )
        except ValueError as exc:
            audit = {
                "event": "telemetry_dropped",
                "reason": "invalid_redaction_policy_signature",
                "connector_id": config.connector_id,
                "agent_call_id": agent_call_id,
            }
            self.history.append(audit)
            return {"posted": False, "audit": audit, "error": str(exc)}

        token = config.oidc.get("access_token") or config.oidc.get("client_secret")
        allowlist = EgressAllowlist(config.portal_base_url, self.agent_endpoint)
        client = PortalClient(config.portal_base_url, allowlist, bearer_token=token)
        record = build_redacted_output_record(
            connector_id=config.connector_id,
            dp_id=dp_id,
            agent_call_id=agent_call_id,
            agent_output_text=agent_output_text,
            policy=policy,
            signer=self.signer,
            agent_endpoint_config={"agent_endpoint": self.agent_endpoint or ""},
            capture_window_start=capture_window_start,
        )
        receipt = client.post_redacted_record(record)
        self.history.append({"event": "redacted_record_posted", "agent_call_id": agent_call_id, "posted": True})
        return {"posted": True, "record": record, "receipt": receipt}

    def _execute_task(self, scan_id: str, task: dict[str, Any], policy: RedactionPolicy, allowlist: EgressAllowlist) -> dict[str, Any]:
        expected_assertions = list(task.get("expected_assertions", []))
        try:
            content = decrypt_task_payload(self.ephemeral_key, task)
            agent_payload = compose_agent_payload(task.get("task_type", "OPAQUE"), json.loads(content.decode("utf-8")))
            agent_response = call_agent(
                self.agent_endpoint or "",
                agent_payload,
                allowlist,
                int(task.get("timeout_seconds", 60)),
            )
            response_text = extract_response_text(agent_response)
            tool_name = extract_tool_name(agent_response)
            response_assertions = extract_assertions(agent_response)
            return build_envelope(
                scan_id=scan_id,
                task_id=task["task_id"],
                endpoint_url=self.agent_endpoint or "",
                auth_method="dp-configured",
                policy=policy,
                signer=self.signer,
                expected_assertions=expected_assertions,
                response_text=response_text,
                tool_name=tool_name,
                response_assertions=response_assertions,
            )
        except Exception as exc:
            return build_envelope(
                scan_id=scan_id,
                task_id=task["task_id"],
                endpoint_url=self.agent_endpoint or "",
                auth_method="dp-configured",
                policy=policy,
                signer=self.signer,
                expected_assertions=expected_assertions,
                response_text=None,
                error_class=classify_error(exc),
                error_detail=str(exc),
            )


def compose_agent_payload(task_type: str, decrypted_content: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_type": task_type,
        "task_id": decrypted_content.get("task_id"),
        "input": decrypted_content.get("input"),
        "metadata": decrypted_content.get("metadata", {}),
    }


def decrypt_task_payload(ephemeral_key: EphemeralKey, task: dict[str, Any]) -> bytes:
    encryption = task.get("encryption", {})
    if encryption.get("sender_public_key"):
        return decrypt_task_content(
            ephemeral_key.private_key,
            encryption["sender_public_key"],
            encryption["nonce"],
            task["encrypted_task_content"],
        )
    return b64url_decode(task["encrypted_task_content"])


def extract_response_text(agent_response: dict[str, Any]) -> str:
    for key in ("response", "output", "text", "message", "answer"):
        value = agent_response.get(key)
        if isinstance(value, str):
            return ensure_json_serialized_under_response_cap(value)
    return ensure_json_serialized_under_response_cap(agent_response)


def extract_tool_name(agent_response: dict[str, Any]) -> str | None:
    value = agent_response.get("tool_called")
    return value if isinstance(value, str) else None


def extract_assertions(agent_response: dict[str, Any]) -> dict[str, Any]:
    value = agent_response.get("assertions")
    return value if isinstance(value, dict) else {}


def classify_error(exc: Exception) -> str:
    if isinstance(exc, OversizedResponseError):
        return "oversized_response"
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "auth" in name or "forbidden" in name or "unauthorized" in name:
        return "auth"
    if "connection" in name or "network" in name:
        return "network"
    if "json" in name or "decode" in name:
        return "invalid_response"
    return "other"
