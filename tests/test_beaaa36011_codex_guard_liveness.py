"""BEAAA-36011: the codex OAuth guard emits a positive liveness receipt.

Why this exists. `are-codex-oauth-guard.sh` writes to its log only when it heals
something or hits a critical, which is good for signal density and useless for
answering "is the guard still running?". A deleted cron entry and a perfectly
healthy fleet produce byte-identical logs. Every prior check in this family could
only detect a fault the guard noticed *while running* — BEAAA-35988 (covering 1 of
21 seats while reporting healthy) and BEAAA-36012 (a silent 22 -> 3 coverage drop)
are both that shape. Absence of complaint was being read as evidence of a pass.

What is pinned here:

1. A successful pass stamps `GUARD_OK_STATE` with the timestamp and counts.
2. `--assert-fresh` passes right after a pass and fails (exit 5) when the receipt
   is absent or older than the threshold.
3. `--assert-fresh` is read-only — it must not heal, or a freshness probe would
   paper over the outage it is supposed to report.
4. A **coverage regression stamps nothing**. This is the load-bearing one: the
   receipt is only meaningful if "fresh" implies "a full pass completed cleanly".
   If the receipt were written before the coverage floor, a guard covering 3 of 22
   seats would keep reporting fresh forever — reintroducing BEAAA-36012's bug with
   extra steps.
5. A dry run stamps nothing. Auditing is not evidence the real thing ran.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "ops" / "paperclip" / "host-scripts" / "are-codex-oauth-guard.sh"


def _fixture(tmp_path: Path, seats: int = 2, link: bool = True) -> dict[str, str]:
    """Sandbox the guard entirely inside tmp_path via its env overrides."""
    target = tmp_path / "shared" / "auth.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"auth_mode": "chatgpt", "tokens": {"access_token": "x"}}', encoding="utf-8")
    agents = tmp_path / "agents"
    for i in range(seats):
        home = agents / f"agent-{i}" / "codex-home"
        home.mkdir(parents=True)
        if link:
            (home / "auth.json").symlink_to(target)
    state = tmp_path / "state"
    state.mkdir()
    return {
        "GUARD_TARGET": str(target),
        "GUARD_LOG": str(state / "guard.log"),
        "GUARD_COVERAGE_STATE": str(state / "guard.coverage"),
        "GUARD_OK_STATE": str(state / "guard.ok"),
        "GUARD_AGENTS_ROOT": str(agents),
    }


def _run(env_over: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **env_over}
    return subprocess.run([str(GUARD), *args], env=env, capture_output=True, text=True, timeout=120)


def test_healthy_pass_writes_a_receipt_with_counts(tmp_path: Path) -> None:
    env = _fixture(tmp_path, seats=3)
    assert _run(env).returncode == 0
    receipt = Path(env["GUARD_OK_STATE"]).read_text(encoding="utf-8")
    assert "checked=3" in receipt, receipt
    assert "healed=0" in receipt, receipt
    assert receipt.startswith("20"), receipt  # ISO-8601 stamp


def test_assert_fresh_passes_immediately_after_a_pass(tmp_path: Path) -> None:
    env = _fixture(tmp_path)
    assert _run(env).returncode == 0
    done = _run(env, "--assert-fresh")
    assert done.returncode == 0, done.stderr
    assert "OK: guard pass" in done.stdout


def test_assert_fresh_fails_with_no_receipt(tmp_path: Path) -> None:
    env = _fixture(tmp_path)
    probe = _run(env, "--assert-fresh")
    assert probe.returncode == 5, probe.stdout + probe.stderr
    assert "STALE" in probe.stderr


def test_assert_fresh_fails_on_a_stale_receipt(tmp_path: Path) -> None:
    env = _fixture(tmp_path)
    assert _run(env).returncode == 0
    ok = Path(env["GUARD_OK_STATE"])
    old = time.time() - 4000
    os.utime(ok, (old, old))
    probe = _run(env, "--assert-fresh=900")
    assert probe.returncode == 5, probe.stdout + probe.stderr
    assert "STALE" in probe.stderr
    # ...and a threshold wide enough to cover it passes, proving the age is what is compared.
    assert _run(env, "--assert-fresh=99999").returncode == 0


def test_assert_fresh_is_read_only_and_does_not_heal(tmp_path: Path) -> None:
    """A freshness probe must never repair; healing here would hide the outage."""
    env = _fixture(tmp_path, seats=1, link=False)
    missing = Path(env["GUARD_AGENTS_ROOT"]) / "agent-0" / "codex-home" / "auth.json"
    assert not missing.exists()
    assert _run(env, "--assert-fresh").returncode == 5
    assert not missing.exists(), "--assert-fresh healed a link; it must be read-only"


def test_coverage_regression_stamps_no_receipt(tmp_path: Path) -> None:
    """The pin that makes 'fresh' mean 'a full pass completed cleanly'.

    Seed the high-water mark above the discoverable seat count: the guard must
    exit 4 and leave no receipt. If the receipt were stamped before the coverage
    floor, a guard silently covering a fraction of the fleet would report fresh
    forever — exactly BEAAA-36012's failure with a green light on top.
    """
    env = _fixture(tmp_path, seats=2)
    Path(env["GUARD_COVERAGE_STATE"]).write_text("9\n", encoding="utf-8")
    run = _run(env)
    assert run.returncode == 4, run.stdout + run.stderr
    assert not Path(env["GUARD_OK_STATE"]).exists(), "coverage regression must not stamp a receipt"


def test_dry_run_stamps_no_receipt(tmp_path: Path) -> None:
    env = _fixture(tmp_path)
    assert _run(env, "--dry-run").returncode == 0
    assert not Path(env["GUARD_OK_STATE"]).exists()


def test_assert_fresh_rejects_a_non_integer_threshold(tmp_path: Path) -> None:
    env = _fixture(tmp_path)
    assert _run(env, "--assert-fresh=soon").returncode == 64


def test_missing_shared_store_stamps_no_receipt(tmp_path: Path) -> None:
    """Exit 2 is a real failure; it must not look like a completed pass."""
    env = _fixture(tmp_path)
    Path(env["GUARD_TARGET"]).unlink()
    assert _run(env).returncode == 2
    assert not Path(env["GUARD_OK_STATE"]).exists()
