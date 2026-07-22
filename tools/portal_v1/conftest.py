import pytest


@pytest.fixture(autouse=True)
def allow_explicit_fixture_scoring(monkeypatch):
    """Portal tests intentionally exercise the deterministic fixture scanner."""
    monkeypatch.setenv("ARE_SCAN_ALLOW_FIXTURE", "1")
