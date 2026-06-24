"""BEAAA-8263 — direct live-bind nft allowlist migration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import api.scan_runtime_factory as scan_runtime_factory
from api.scan_runtime_factory import (
    build_v0_corpus_tarball,
    make_production_scan_runtime,
    v0_corpus_snapshot_hash,
)
from harness import egress
from scanners.live_probe_orchestrator import (
    LIVE_PROBE_VARIANCE_BAND,
    DimResult,
    OrchestratorError,
    ScanRequest,
    ScanResult,
)


def _nft_list_output(*, handle: int = 7, comment: str = "") -> str:
    return json.dumps({
        "nftables": [{
            "rule": {
                "family": "inet",
                "table": "are_egress_filter",
                "chain": "output",
                "handle": handle,
                "comment": comment,
                "expr": [],
            },
        }],
    })


def _scan_result(scan_id: str) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="complete",
        scanner_version="0.4.2",
        corpus_version="0.1.0",
        rows_emitted=1,
        k_replicates=5,
        live_probe_variance_band=LIVE_PROBE_VARIANCE_BAND,
        dim_results=(
            DimResult(dim_id="P1.1", raw=3.0, spread=0.0, replicates=(3.0,) * 5),
        ),
        spread_per_dim={"P1.1": 0.0},
        pillar_spread={"P1": 0.0},
        total_spread=0.0,
        incomplete=False,
        incomplete_reason=None,
    )


def _scan_request(corpus_hash: str) -> ScanRequest:
    return ScanRequest(
        scan_id="scan-beaaa8263",
        tenant_id="tenant-beaaa8263",
        agent_id="agent-beaaa8263",
        agent_endpoint="https://apichat.syllable.cloud",
        credential_ref="cred-beaaa8263",
        corpus_snapshot_hash=corpus_hash,
        scanner_version="0.4.2",
        corpus_version="0.1.0",
        seed=42,
        scan_mode="live_probe",
        k_replicates=5,
        invocation_context={"purpose": "pre_bind"},
    )


def test_direct_egress_env_alias_adds_root_only_rule_and_removes_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(egress.DIRECT_EGRESS_NFT_ENV, "1")
    monkeypatch.setattr(egress.time, "time", lambda: 1_000)
    calls: list[list[str]] = []
    comments: list[str] = []

    def exec_cmd(cmd: list[str]) -> str:
        calls.append(cmd)
        if cmd[:3] == ["nft", "add", "rule"]:
            comments.append(json.loads(cmd[-1]))
            return ""
        if cmd[:4] == ["nft", "-j", "-a", "list"]:
            return _nft_list_output(comment=comments[-1] if comments else "")
        return ""

    with egress.ScopedEgressAllowlist(
        "https://apichat.syllable.cloud",
        live_bind_scope_id="scan-beaaa8263",
        live_bind_resolver=lambda _host, _port: ("57.152.30.223",),
        live_bind_command_executor=exec_cmd,
    ):
        pass

    add_cmd = next(cmd for cmd in calls if cmd[:3] == ["nft", "add", "rule"])
    assert add_cmd[:6] == ["nft", "add", "rule", "inet", "are_egress_filter", "output"]
    assert add_cmd[6:15] == [
        "ip", "daddr", "57.152.30.223", "tcp", "dport", "443", "meta", "skuid", "0",
    ]
    assert add_cmd[-3:] == ["accept", "comment", json.dumps(comments[0])]
    assert "BEAAA-8263-direct-live-bind" in comments[0]
    assert "scan_id=scan-beaaa8263" in comments[0]
    assert "host=apichat.syllable.cloud" in comments[0]
    assert "expires=4600" in comments[0]
    assert [
        "nft", "delete", "rule", "inet", "are_egress_filter", "output",
        "handle", "7",
    ] in calls


def test_direct_live_bind_rejects_private_dns_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(egress.DIRECT_EGRESS_NFT_ENV, "1")

    with pytest.raises(egress.EgressNotAllowed, match="forbidden address"):
        with egress.ScopedEgressAllowlist(
            "https://apichat.syllable.cloud",
            live_bind_scope_id="scan-beaaa8263",
            live_bind_resolver=lambda _host, _port: ("10.0.0.7",),
            live_bind_command_executor=lambda _cmd: "",
        ):
            pass


class _ExitedProcess:
    exitcode = 0

    def is_alive(self) -> bool:
        return False

    def terminate(self) -> None:  # pragma: no cover
        return None

    def join(self, timeout: float | None = None) -> None:  # pragma: no cover
        return None

    def close(self) -> None:  # pragma: no cover
        return None


def test_runtime_parent_owns_live_bind_scope_on_prober_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_tarball_path = build_v0_corpus_tarball(tmp_path / "corpus")
    corpus_hash = v0_corpus_snapshot_hash(corpus_tarball_path)
    events: list[str] = []

    class _Scope:
        def __init__(self, allowed_endpoint: str, **kwargs: Any):
            events.append(
                f"construct:{allowed_endpoint}:{kwargs.get('live_bind_scope_id')}"
            )
            self._scope_id = kwargs.get("live_bind_scope_id")

        def __enter__(self):
            if self._scope_id:
                events.append("enter-live-bind")
            return self

        def __exit__(self, *exc):
            if self._scope_id:
                events.append("exit-live-bind")
            return None

    def fake_spawn_scorer(**_: Any) -> _ExitedProcess:
        events.append("scorer")
        return _ExitedProcess()

    def fake_prober_target(
        *,  # noqa: ARG001
        scan_request: ScanRequest,
        result_queue: Any,
        **_: Any,
    ) -> None:
        result_queue.put(("err", "RuntimeError", f"forced {scan_request.scan_id}"))

    monkeypatch.setattr(scan_runtime_factory, "ScopedEgressAllowlist", _Scope)
    monkeypatch.setattr(scan_runtime_factory, "spawn_scorer", fake_spawn_scorer)
    monkeypatch.setattr(scan_runtime_factory, "_prober_target", fake_prober_target)

    runtime = make_production_scan_runtime(
        corpus_tarball_path=corpus_tarball_path,
        runner_factory=lambda *_: None,
        probe_run_fn=lambda _runner: iter(()),
    )

    with pytest.raises(OrchestratorError, match="forced scan-beaaa8263"):
        runtime._run_scan_fn(_scan_request(corpus_hash), "secret-beaaa8263")

    assert events == [
        "scorer",
        "construct:https://apichat.syllable.cloud:scan-beaaa8263",
        "enter-live-bind",
        "exit-live-bind",
    ]


def test_runtime_parent_owns_live_bind_scope_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_tarball_path = build_v0_corpus_tarball(tmp_path / "corpus")
    corpus_hash = v0_corpus_snapshot_hash(corpus_tarball_path)
    events: list[str] = []

    class _Scope:
        def __init__(self, allowed_endpoint: str, **kwargs: Any):
            del allowed_endpoint
            self._scope_id = kwargs.get("live_bind_scope_id")

        def __enter__(self):
            if self._scope_id:
                events.append("enter-live-bind")
            return self

        def __exit__(self, *exc):
            if self._scope_id:
                events.append("exit-live-bind")
            return None

    def fake_spawn_scorer(**_: Any) -> _ExitedProcess:
        return _ExitedProcess()

    def fake_prober_target(
        *,  # noqa: ARG001
        scan_request: ScanRequest,
        result_queue: Any,
        **_: Any,
    ) -> None:
        result_queue.put(("ok", _scan_result(scan_request.scan_id)))

    monkeypatch.setattr(scan_runtime_factory, "ScopedEgressAllowlist", _Scope)
    monkeypatch.setattr(scan_runtime_factory, "spawn_scorer", fake_spawn_scorer)
    monkeypatch.setattr(scan_runtime_factory, "_prober_target", fake_prober_target)

    runtime = make_production_scan_runtime(
        corpus_tarball_path=corpus_tarball_path,
        runner_factory=lambda *_: None,
        probe_run_fn=lambda _runner: iter(()),
    )

    result = runtime._run_scan_fn(_scan_request(corpus_hash), "secret-beaaa8263")

    assert result.scan_id == "scan-beaaa8263"
    assert events == ["enter-live-bind", "exit-live-bind"]
