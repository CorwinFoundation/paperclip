"""BEAAA-19052 shared, restart-safe promotion lease authority."""
from __future__ import annotations

import threading

import pytest

from tools.tranche1_canary_runner import (
    LEASE_AUTHORITY_SCHEMA_VERSION,
    Tranche1CanaryError,
    consume_promotion_lease_postgres,
)

AUTHORITY = {
    "lease_id": "lease-one",
    "signature": "ed25519:" + "a" * 86,
    "commit_sha": "b" * 40,
    "image_ref": "scanner@sha256:" + "c" * 64,
    "tenant_id": "tenant",
    "environment": "production",
}


class _SharedDatabase:
    def __init__(self) -> None:
        self.identity = ("authority-prod-1", "recovery-7", LEASE_AUTHORITY_SCHEMA_VERSION)
        self.health = (False, "off")
        self.consumed: set[str] = set()
        self.lock = threading.Lock()
        self.outage = False

    def connect(self, _dsn: str, **_kwargs):
        if self.outage:
            raise OSError("database unavailable")
        return _Connection(self)


class _Connection:
    def __init__(self, database: _SharedDatabase) -> None:
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _Cursor(self.database)

    def close(self):
        pass


class _Cursor:
    def __init__(self, database: _SharedDatabase) -> None:
        self.database = database
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, params=None):
        if "authority_identity" in sql:
            self.result = self.database.identity
        elif "pg_is_in_recovery" in sql:
            self.result = self.database.health
        elif "INSERT INTO" in sql:
            scope = params[0]
            with self.database.lock:
                if scope in self.database.consumed:
                    self.result = None
                else:
                    self.database.consumed.add(scope)
                    self.result = (scope,)

    def fetchone(self):
        return self.result


def _consume(database: _SharedDatabase, **overrides):
    values = {
        "authority": AUTHORITY,
        "dsn": "postgresql://shared-authority",
        "expected_storage_identity": "authority-prod-1",
        "expected_continuity_epoch": "recovery-7",
        "connect": database.connect,
    }
    values.update(overrides)
    return consume_promotion_lease_postgres(**values)


def test_restart_and_independent_replicas_share_one_consumption():
    database = _SharedDatabase()
    first_scope = _consume(database)
    with pytest.raises(Tranche1CanaryError, match="already been consumed"):
        _consume(database)
    assert database.consumed == {first_scope}


def test_concurrent_processes_atomically_allow_one_consumption():
    database = _SharedDatabase()
    barrier = threading.Barrier(8)
    outcomes = []

    def attempt():
        barrier.wait()
        try:
            _consume(database)
        except Tranche1CanaryError as exc:
            outcomes.append(str(exc))
        else:
            outcomes.append("consumed")

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("consumed") == 1
    assert outcomes.count("promotion lease has already been consumed") == 7


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda db: setattr(db, "identity", None), "identity or continuity mismatch"),
        (lambda db: setattr(db, "identity", ("replacement", "recovery-7", 1)), "identity or continuity mismatch"),
        (lambda db: setattr(db, "identity", ("authority-prod-1", "stale", 1)), "identity or continuity mismatch"),
        (lambda db: setattr(db, "health", (True, "on")), "not a writable primary"),
        (lambda db: setattr(db, "outage", True), "failed closed"),
    ],
)
def test_volume_loss_identity_mismatch_stale_restore_or_outage_fails_closed(mutation, message):
    database = _SharedDatabase()
    mutation(database)
    with pytest.raises(Tranche1CanaryError, match=message):
        _consume(database)
    assert database.consumed == set()


def test_missing_replica_binding_fails_before_connect():
    database = _SharedDatabase()
    with pytest.raises(Tranche1CanaryError, match="binding is incomplete"):
        _consume(database, expected_storage_identity="")
    assert database.consumed == set()
