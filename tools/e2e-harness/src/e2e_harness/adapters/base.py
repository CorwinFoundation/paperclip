"""Capture adapters: vendor export -> normalized session -> schema-v1.0 manifest.

(BEAAA-7427, Done #2.)

A capture adapter is the per-vendor seam. It knows how to pull a vendor's
transcripts / logs / recordings out of that vendor's API and normalize them
into a vendor-agnostic ``CapturedSession`` (a list of ``Turn``). The shared
``session_to_manifest`` then renders the session to a Log-Event-Schema-v1.0
manifest using the *same* capture-time redaction + injection-feature
extraction the internal dogfood tap uses (BEAAA-7320 / 7346), so the Monitor
post-bind pipeline ingests it byte-identically to a real customer push.

Redaction is applied on receipt (raw transcript text is never retained);
prompt-injection markers are computed *before* redaction and preserved as
structured ``detector_features`` so the P5 detector still fires on the
redacted event — exactly the dogfood invariant.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..scenario_kit import Turn

# Reuse the shipped dogfood capture primitives (single source of truth for the
# schema-v1.0 event shape, redaction policy and signature).
from post_bind_ingest.dogfood_tap import (  # type: ignore
    SCHEMA_VERSION,
    MANIFEST_VERSION,
    DOGFOOD_TAP_VERSION,
    CAPTURE_REDACTION_POLICY,
    _redacted_event,
    _sha256_signature,
    _prompt_injection_feature_markers,
)

# Deterministic capture clock base. We never call Date.now(): timestamps are
# derived from a fixed base + per-event offset so the same session always
# produces the same manifest (reproducibility, CTO Supreme Law).
_CAPTURE_CLOCK_BASE = "2026-06-22T00:00:00Z"


@dataclass(frozen=True)
class CapturedSession:
    """Vendor-agnostic capture of one agent conversation under test."""

    session_id: str
    agent_id: str
    vendor: str
    turns: tuple[Turn, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


def _event_id(vendor: str, session_id: str, idx: int, speaker: str, tool: str | None) -> str:
    seed = f"{vendor}:{session_id}:{idx}:{speaker}:{tool or '-'}"
    return f"{vendor}-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def _clock(idx: int) -> str:
    # 2026-06-22T00:00:NNZ — deterministic, monotonically increasing.
    return f"2026-06-22T00:00:{idx % 60:02d}Z"


def _markers(*texts: Any) -> tuple[str, ...]:
    found: set[str] = set()
    for text in texts:
        if isinstance(text, str) and text.strip():
            found.update(_prompt_injection_feature_markers(text))
    return tuple(sorted(found))


def session_to_events(session: CapturedSession) -> list[dict[str, Any]]:
    """Render a captured session to redacted schema-v1.0 events."""

    events: list[dict[str, Any]] = []
    for idx, turn in enumerate(session.turns):
        is_action = bool(turn.tool_name) and turn.tool_input is not None and turn.speaker == "agent"
        # Agent tool call (the surface that carries unsafe actions + injection).
        if is_action:
            payload: dict[str, Any] = {
                "agent_invocation_id": session.session_id,
                "source": session.vendor,
                "tool": turn.tool_name,
                "tool_call_id": _event_id(session.vendor, session.session_id, idx, "agent", turn.tool_name),
                "tool_input": turn.tool_input,
            }
            markers = _markers(turn.text, *(_iter_strings(turn.tool_input)))
            if markers:
                payload["detector_features"] = {"prompt_injection_markers": markers}
            events.append(
                _redacted_event(
                    event_id=_event_id(session.vendor, session.session_id, idx, "agent", turn.tool_name),
                    event_type="tool_call",
                    logged_at=_clock(idx),
                    payload=payload,
                )
            )
            continue
        # User / tool turn -> source reference (carries injected instructions).
        if turn.speaker in {"user", "tool"}:
            text = turn.text or ""
            tool_result = turn.tool_result
            payload = {
                "agent_invocation_id": session.session_id,
                "source": session.vendor,
                "source_reference_id": _event_id(session.vendor, session.session_id, idx, turn.speaker, turn.tool_name),
                "role": turn.speaker,
                "content_type": "tool_result" if turn.speaker == "tool" else "prompt",
                "content": text,
            }
            if tool_result is not None:
                payload["tool_result"] = tool_result
            markers = _markers(text, tool_result)
            if markers:
                payload["detector_features"] = {"prompt_injection_markers": markers}
            events.append(
                _redacted_event(
                    event_id=payload["source_reference_id"],
                    event_type="tool_source_reference",
                    logged_at=_clock(idx),
                    payload=payload,
                )
            )
            continue
        # Plain agent text -> completion.
        events.append(
            _redacted_event(
                event_id=_event_id(session.vendor, session.session_id, idx, "agent", None),
                event_type="llm_completion",
                logged_at=_clock(idx),
                payload={
                    "agent_invocation_id": session.session_id,
                    "source": session.vendor,
                    "role": "agent",
                    "content_type": "text",
                    "content": turn.text or "",
                },
            )
        )
    return events


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _iter_strings(v)


def session_to_manifest(
    session: CapturedSession,
    *,
    customer_id: str = "backbond-e2e-test-target",
    mode: str = "shadow",
) -> dict[str, Any]:
    """Build a signed schema-v1.0 manifest from a captured session.

    The manifest is structurally identical to the internal dogfood manifest
    (same ``manifest_version`` / ``schema_version`` / signature scheme) so the
    Monitor normalizer + detectors treat it exactly like real pushed traffic.
    """

    events = session_to_events(session)
    if not events:
        raise ValueError("captured session produced no schema-v1.0 events")
    manifest_seed = f"{session.vendor}:{session.session_id}:{len(events)}"
    manifest_id = f"e2e-{mode}-{hashlib.sha256(manifest_seed.encode('utf-8')).hexdigest()[:16]}"
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "manifest_id": manifest_id,
        "customer_id": customer_id,
        "schema_version": SCHEMA_VERSION,
        "issued_at": _CAPTURE_CLOCK_BASE,
        "events": events,
        "metadata": {
            "e2e_test_target": True,
            "dogfood": False,
            "mode": mode,
            "vendor": session.vendor,
            "tap_version": DOGFOOD_TAP_VERSION,
            "harness": "e2e-harness@0.1.0",
            "retained_raw_transcripts": False,
            "redaction_policy": CAPTURE_REDACTION_POLICY,
            "session_metadata": session.metadata,
        },
    }
    manifest["signature"] = _sha256_signature(manifest)
    return manifest


class CaptureAdapter(ABC):
    """Per-vendor adapter: raw vendor export -> CapturedSession.

    Concrete adapters implement :meth:`to_session` for one vendor's transcript
    export format. The live exporter (``fetch``) is intentionally a thin,
    credential-gated stub until the Founder picks a vendor and shares scoped
    credentials — the normalization logic (the part that takes build time) is
    what's pre-built here.
    """

    vendor: str = "abstract"
    #: Human pointer to the vendor's transcript/recording export API.
    export_api_doc: str = ""

    @abstractmethod
    def to_session(self, raw_export: dict[str, Any]) -> CapturedSession:
        """Normalize one raw vendor transcript export into a CapturedSession."""

    def fetch(self, credentials: dict[str, Any], **params: Any) -> list[dict[str, Any]]:  # pragma: no cover - live path
        """Pull raw transcript exports from the vendor API.

        Blocked-until-Founder: needs scoped credentials + a chosen vendor.
        Raises until a live run is authorized so a guess can't silently ship.
        """
        raise NotImplementedError(
            f"{self.vendor} live export is gated on Founder vendor pick + scoped "
            f"credentials (BEAAA-7424). See {self.export_api_doc}"
        )

    def capture(self, raw_export: dict[str, Any], *, mode: str = "shadow") -> dict[str, Any]:
        """raw vendor export -> signed schema-v1.0 manifest."""
        return session_to_manifest(self.to_session(raw_export), mode=mode)
