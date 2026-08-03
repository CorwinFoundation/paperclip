from __future__ import annotations

import importlib
import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urlparse


FEATURE_FLAG = "BACKBOND_OPENLLMETRY_COMPAT"
DEFAULT_ENDPOINT = "http://127.0.0.1:4318"
SUPPORTED_FRAMEWORKS = frozenset({"crewai", "langchain"})


class OpenLLMetryCompatibilityError(ValueError):
    """Fail-closed compatibility configuration error."""


@dataclass(frozen=True)
class CompatibilityConfig:
    enabled: bool
    endpoint: str
    content_capture: bool = False
    upstream_telemetry: bool = False

    @classmethod
    def from_env(cls) -> "CompatibilityConfig":
        enabled = os.getenv(FEATURE_FLAG, "off").strip().lower() in {"1", "on", "true"}
        endpoint = os.getenv("BACKBOND_OTLP_LOOPBACK_ENDPOINT", DEFAULT_ENDPOINT)
        _require_loopback(endpoint)
        return cls(enabled=enabled, endpoint=endpoint)


def _require_loopback(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise OpenLLMetryCompatibilityError("OTLP endpoint must be an HTTP(S) URL")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise OpenLLMetryCompatibilityError("OTLP endpoint must use a loopback IP literal") from exc
    if not address.is_loopback:
        raise OpenLLMetryCompatibilityError("non-loopback OTLP endpoint rejected")


def hardened_environment(config: CompatibilityConfig) -> dict[str, str]:
    """Return the complete no-hosted-backend, no-content exporter environment."""
    _require_loopback(config.endpoint)
    return {
        "TRACELOOP_BASE_URL": "",
        "TRACELOOP_TELEMETRY": "false",
        "TRACELOOP_TRACE_CONTENT": "false",
        "CREWAI_DISABLE_TELEMETRY": "true",
        "OTEL_SDK_DISABLED": "false" if config.enabled else "true",
        "OTEL_TRACES_EXPORTER": "otlp" if config.enabled else "none",
        "OTEL_EXPORTER_OTLP_ENDPOINT": config.endpoint,
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    }


class FrameworkInstrumentation:
    """Feature-gated OpenLLMetry instrumentor with symmetric rollback."""

    _IMPORTS = {
        "crewai": ("opentelemetry.instrumentation.crewai", "CrewAIInstrumentor"),
        "langchain": ("opentelemetry.instrumentation.langchain", "LangchainInstrumentor"),
    }

    def __init__(self, framework: str, config: CompatibilityConfig) -> None:
        if framework not in SUPPORTED_FRAMEWORKS:
            raise OpenLLMetryCompatibilityError(f"unsupported framework: {framework}")
        self.framework = framework
        self.config = config
        self._instrumentor = None

    def enable(self) -> bool:
        if not self.config.enabled:
            return False
        for key, value in hardened_environment(self.config).items():
            os.environ[key] = value
        module_name, class_name = self._IMPORTS[self.framework]
        instrumentor_type = getattr(importlib.import_module(module_name), class_name)
        self._instrumentor = instrumentor_type()
        self._instrumentor.instrument()
        return True

    def disable(self) -> None:
        if self._instrumentor is not None:
            self._instrumentor.uninstrument()
            self._instrumentor = None

    def __enter__(self) -> "FrameworkInstrumentation":
        self.enable()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.disable()
