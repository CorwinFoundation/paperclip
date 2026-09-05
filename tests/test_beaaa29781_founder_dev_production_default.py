"""BEAAA-29781: the registry report must not exit non-zero at the production default.

Founder directive BEAAA-29856 settles what the refusal meant: the scanner server
runs with ``BACKBOND_FOUNDER_DEV_MODE=1`` and dev-promoted families are run and
scored there, so a live dev-mode-scored family is expected state, not a fault the
report should refuse over. ``main()`` discloses the condition on stderr and still
emits the count. ``--check``, the fail-closed publish gate, is unchanged, and so
is ``assert_production_attested`` itself.
"""

from __future__ import annotations

import pytest

from tools.active_test_registry import main
from tools.founder_dev_mode import FounderDevModeError, assert_production_attested

DEV_MODE_ENV = "BACKBOND_FOUNDER_DEV_MODE"


def _run(monkeypatch, argv, dev_mode):
    if dev_mode is None:
        monkeypatch.delenv(DEV_MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(DEV_MODE_ENV, dev_mode)
    monkeypatch.setattr("sys.argv", ["active_test_registry", *argv])
    return main()


def test_report_exits_zero_at_the_production_default(monkeypatch):
    """The reproduce case on the ticket: flag unset, plain report, exit 0."""
    assert _run(monkeypatch, [], None) == 0


def test_report_exits_zero_with_the_server_setting(monkeypatch):
    """The scanner server sets the flag to 1; the report must not refuse there either."""
    assert _run(monkeypatch, [], "1") == 0


def test_check_still_refuses_an_unattested_scored_family(monkeypatch, capsys):
    """--check stays fail-closed. This fires today, so it is a control, not a tautology."""
    assert _run(monkeypatch, ["--check"], None) == 1
    assert "production_boundary_refused" in capsys.readouterr().out


def test_the_guard_itself_is_unchanged():
    """Only the CLI exit coupling moved; the boundary predicate still fails closed."""
    with pytest.raises(FounderDevModeError):
        assert_production_attested(
            [{"test_id": "t1", "scoring_active": True, "attestation_tier": "founder_dev_mode"}]
        )
    assert_production_attested(
        [{"test_id": "t2", "scoring_active": True, "attestation_tier": "production_signed"}]
    )
