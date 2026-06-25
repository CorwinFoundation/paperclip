"""Config loader — reads are-probe.yaml and validates required fields.

R1 fail-closed posture: any missing field that affects envelope reproducibility
or the no-telephone-home invariant raises ConfigError before any probe runs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TargetConfig:
    url: str
    shape: str
    auth_mode: str
    bearer_env_var: str | None
    timeout_seconds: int
    response_json_paths: tuple[str, ...]


@dataclass(frozen=True)
class ProbeFamilyConfig:
    family: str
    corpus_pack: str


@dataclass(frozen=True)
class OutputConfig:
    envelope_path: str
    freeze_timestamp: bool


@dataclass(frozen=True)
class SigningConfig:
    sign: bool
    key_id: str
    key_path: str


@dataclass(frozen=True)
class ProbeConfig:
    probe_sdk_version: str
    probe_sdk_release: str
    target: TargetConfig
    scanner_version: str
    corpus_version: str
    probe_set_version: str
    seed: int
    model_versions: dict
    probes: list[ProbeFamilyConfig]
    output: OutputConfig
    signing: SigningConfig
    scanner_bin: str
    telemetry_enabled: bool


def load_config(path: str | Path) -> ProbeConfig:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ConfigError(f"config not found: {p}")
    raw = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    target_raw = raw.get("target") or {}
    target_shape = target_raw.get("shape", "generic_prompt_output")
    if target_shape not in ("generic_prompt_output", "botpress_webchat", "transcript_export"):
        raise ConfigError(
            "target.shape must be one of: generic_prompt_output, "
            "botpress_webchat, transcript_export"
        )
    auth_raw = target_raw.get("auth") or {}
    auth_mode = auth_raw.get("mode", "none")
    if auth_mode not in ("none", "bearer"):
        raise ConfigError(f"target.auth.mode={auth_mode!r} not in (none, bearer)")

    target = TargetConfig(
        url=_require_str(target_raw, "url", path="target.url"),
        shape=target_shape,
        auth_mode=auth_mode,
        bearer_env_var=auth_raw.get("bearer_env_var"),
        timeout_seconds=int(target_raw.get("timeout_seconds", 30)),
        response_json_paths=_response_paths(target_raw),
    )

    probes_raw = raw.get("probes") or []
    if not probes_raw:
        raise ConfigError("probes: empty — R1 requires at least one probe family")
    probes = [
        ProbeFamilyConfig(family=_require_str(p, "family", path="probes[].family"),
                          corpus_pack=_require_str(p, "corpus_pack",
                                                   path="probes[].corpus_pack"))
        for p in probes_raw
    ]

    out_raw = raw.get("output") or {}
    output = OutputConfig(
        envelope_path=_require_str(out_raw, "envelope_path",
                                   path="output.envelope_path"),
        freeze_timestamp=bool(out_raw.get("freeze_timestamp", False)),
    )

    sig_raw = raw.get("signing") or {}
    signing = SigningConfig(
        sign=bool(sig_raw.get("sign", False)),
        key_id=sig_raw.get("key_id", "cto-attest-v1"),
        key_path=sig_raw.get("key_path", "~/.config/backbond/keys/cto-attest-v1.json"),
    )

    scanner_bin_env = raw.get("scanner_bin_env_var", "ARE_SCAN_BIN")
    scanner_bin = os.environ.get(
        scanner_bin_env,
        raw.get("scanner_bin_default", ""),
    )
    if not scanner_bin:
        raise ConfigError(
            f"scanner bin not resolved (env {scanner_bin_env!r} unset and no default)"
        )

    telemetry_raw = raw.get("telemetry") or {}
    if bool(telemetry_raw.get("enabled", False)):
        # Hard reject — Founder freeze. R1 invariant: zero telephone-home.
        raise ConfigError(
            "telemetry.enabled=true rejected: R1 ships under Founder comms-freeze "
            "with strict no-telephone-home posture (cto-internal-directive-2026-06-13 §3)."
        )

    return ProbeConfig(
        probe_sdk_version=_require_str(raw, "probe_sdk_version"),
        probe_sdk_release=_require_str(raw, "probe_sdk_release"),
        target=target,
        scanner_version=_require_str(raw, "scanner_version"),
        corpus_version=_require_str(raw, "corpus_version"),
        probe_set_version=_require_str(raw, "probe_set_version"),
        seed=int(_require(raw, "seed")),
        model_versions=raw.get("model_versions") or {"primary": "unknown", "judges": []},
        probes=probes,
        output=output,
        signing=signing,
        scanner_bin=scanner_bin,
        telemetry_enabled=False,
    )


def _require(d, key, *, path: str | None = None):
    label = path or key
    if key not in d or d[key] is None:
        raise ConfigError(f"missing required field: {label}")
    return d[key]


def _require_str(d, key, *, path: str | None = None):
    label = path or key
    v = _require(d, key, path=label)
    if not isinstance(v, str) or not v:
        raise ConfigError(f"{label} must be a non-empty string")
    return v


def _response_paths(target_raw: dict) -> tuple[str, ...]:
    raw = target_raw.get("response_json_paths")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("target.response_json_paths must be a list of strings")
    out = []
    for item in raw:
        if not isinstance(item, str) or not item:
            raise ConfigError("target.response_json_paths entries must be non-empty strings")
        out.append(item)
    return tuple(out)
