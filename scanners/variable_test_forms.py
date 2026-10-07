"""Private, durable equivalent-form assembly for Starter and Full Scan.

Selection changes scenarios, not rubrics. Authored/calibrated scenario content
is supplied by the caller; this module does not claim that random selection
establishes statistical equivalence or creates additional unique tests.
"""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

FORM_VERSION = "variable-test-form/v1"
PRODUCTS = frozenset({"starter", "full"})


class FormError(ValueError):
    """Invalid content, unavailable coverage, or damaged private evidence."""


class InsufficientScenarios(FormError):
    """A required stratum cannot be filled without reusing exposed scenarios."""


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise FormError("form content must be finite JSON data") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _nonempty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise FormError(f"{name} must be a nonempty string")


@dataclass(frozen=True)
class Stratum:
    stratum_id: str
    family_id: str
    rubric_id: str
    rubric_version: str
    difficulty: str
    severity: str
    count: int


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    stratum_id: str
    payload: Mapping[str, Any]
    calibration_status: str = "uncalibrated"
    test_spec_id: str = ""


def _wire_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    """Fingerprint what the target sees; judge metadata cannot evade dedupe."""
    return {
        key: payload[key]
        for key in ("prompt", "system", "turns", "pair_turns", "setup", "tools")
        if key in payload
    }


def target_input_hash(payload: Mapping[str, Any]) -> str:
    """Hash exactly the target-facing fields used for exposure deduplication."""
    return _digest(_wire_inputs(dict(payload)))


def _validate_bank(
    strata: Sequence[Stratum], scenarios: Sequence[Scenario], *, require_calibrated: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not strata:
        raise FormError("at least one coverage stratum is required")
    requirements = []
    ids: set[str] = set()
    for stratum in strata:
        row = asdict(stratum)
        for key in ("stratum_id", "family_id", "rubric_id", "rubric_version", "difficulty", "severity"):
            _nonempty(row[key], key)
        if isinstance(stratum.count, bool) or not isinstance(stratum.count, int) or stratum.count < 1:
            raise FormError("stratum count must be a positive integer")
        if stratum.stratum_id in ids:
            raise FormError("duplicate stratum ID")
        ids.add(stratum.stratum_id)
        requirements.append(row)
    rows = []
    scenario_ids: set[str] = set()
    input_hashes: set[str] = set()
    for scenario in scenarios:
        _nonempty(scenario.scenario_id, "scenario_id")
        _nonempty(scenario.test_spec_id, "test_spec_id")
        _nonempty(scenario.calibration_status, "calibration_status")
        if scenario.scenario_id in scenario_ids:
            raise FormError("duplicate scenario ID")
        if scenario.stratum_id not in ids:
            raise FormError("scenario has an undeclared stratum")
        if not isinstance(scenario.payload, Mapping) or not scenario.payload:
            raise FormError("scenario payload must be a nonempty object")
        payload = json.loads(_json(dict(scenario.payload)))
        prompt = payload.get("prompt")
        turns = payload.get("turns")
        if prompt is not None:
            _nonempty(prompt, "prompt")
        elif not isinstance(turns, list) or not turns or not all(isinstance(t, str) and t.strip() for t in turns):
            raise FormError("scenario requires a prompt or nonempty text turns")
        fingerprint = target_input_hash(payload)
        if fingerprint in input_hashes:
            raise FormError("duplicate target-facing content; relabeling does not create a scenario")
        scenario_ids.add(scenario.scenario_id)
        input_hashes.add(fingerprint)
        if require_calibrated and scenario.calibration_status != "calibrated":
            continue
        rows.append({
            "scenario_id": scenario.scenario_id,
            "test_spec_id": scenario.test_spec_id,
            "stratum_id": scenario.stratum_id,
            "payload": payload,
            "calibration_status": scenario.calibration_status,
            "input_hash": fingerprint,
        })
    return sorted(requirements, key=lambda r: r["stratum_id"]), sorted(rows, key=lambda r: r["scenario_id"])


class FormStore:
    """Reserve a form before dispatch and replay its exact private payloads.

    Keep this SQLite file in a restricted evidence directory. Never put the
    returned manifest in a customer report: it contains prompts, answers and
    the private selection seed. The store is independent of scoring math.
    """

    def __init__(self, sqlite_path: str | Path) -> None:
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS variable_test_forms (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                form_id TEXT NOT NULL UNIQUE,
                scope_hash TEXT NOT NULL,
                product TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                manifest TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS variable_form_history ON variable_test_forms(scope_hash, product, sequence)")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _scope(scope: tuple[str, str]) -> str:
        if not isinstance(scope, tuple) or len(scope) != 2:
            raise FormError("scope must be the trusted (tenant_id, agent_id) tuple")
        for value in scope:
            _nonempty(value, "scope identity")
        return _digest(scope)

    @staticmethod
    def _read(row: tuple[str, str]) -> dict[str, Any]:
        content_hash, encoded = row
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != content_hash:
            raise FormError("private form content hash mismatch")
        try:
            return json.loads(encoded)
        except ValueError as exc:
            raise FormError("private form is unreadable") from exc

    def get(self, form_id: str, *, scope: tuple[str, str], product: str) -> dict[str, Any]:
        """Tenant-bound replay. Read-only; never regenerate from today's bank."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT content_hash, manifest FROM variable_test_forms WHERE form_id=? AND scope_hash=? AND product=?",
                (form_id, self._scope(scope), product),
            ).fetchone()
        if row is None:
            raise FormError("private form not found for this scope and product")
        return self._read(row)

    def create(
        self,
        *,
        scope: tuple[str, str],
        product: str,
        bank_version: str,
        strata: Sequence[Stratum],
        scenarios: Sequence[Scenario],
        history_window: int = 1,
        require_calibrated: bool = False,
        form_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically select fresh scenarios with exact coverage or fail.

        ``form_id`` is a server job ID for retry idempotence. There is deliberately
        no caller-supplied seed. Use ``get`` to replay a persisted form after a
        bank revision. Calibration remains the content owner's responsibility.
        """
        if product not in PRODUCTS:
            raise FormError("product must be the stable starter or full identity")
        _nonempty(bank_version, "bank_version")
        if isinstance(history_window, bool) or not isinstance(history_window, int) or history_window < 1:
            raise FormError("history_window must be a positive integer")
        if not isinstance(require_calibrated, bool):
            raise FormError("require_calibrated must be a boolean")
        scope_hash = self._scope(scope)
        requirements, candidates = _validate_bank(strata, scenarios, require_calibrated=require_calibrated)
        for requirement in requirements:
            supply = sum(c["stratum_id"] == requirement["stratum_id"] for c in candidates)
            minimum = (history_window + 1) * requirement["count"]
            if supply < minimum:
                raise InsufficientScenarios(
                    f"stratum {requirement['stratum_id']} needs at least {minimum} scenarios to sustain fresh forms; {supply} supplied"
                )
        bank_hash = _digest({"strata": requirements, "scenarios": candidates})
        policy = {"bank_version": bank_version, "bank_hash": bank_hash,
                  "strata": requirements, "history_window": history_window,
                  "require_calibrated": require_calibrated}
        policy_hash = _digest(policy)
        if form_id is None:
            form_id = str(uuid.uuid4())
        _nonempty(form_id, "form_id")
        with self._connection() as conn:
            # Serializes reservations so concurrent scans cannot both select
            # the same unexposed scenarios. Failed assembly rolls back cleanly.
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT scope_hash, product, content_hash, manifest FROM variable_test_forms WHERE form_id=?", (form_id,)
            ).fetchone()
            if existing:
                if existing[:2] != (scope_hash, product):
                    raise FormError("form ID belongs to another scope or product")
                manifest = self._read(existing[2:])
                if manifest["policy_hash"] != policy_hash:
                    raise FormError("retry changed the form policy; replay the stored form")
                return manifest
            history = conn.execute(
                "SELECT content_hash, manifest FROM variable_test_forms WHERE scope_hash=? AND product=? ORDER BY sequence DESC LIMIT ?",
                (scope_hash, product, history_window),
            ).fetchall()
            exposed_ids: set[str] = set()
            exposed_inputs: set[str] = set()
            prior_form_ids = []
            for row in history:
                previous = self._read(row)
                prior_form_ids.append(previous["form_id"])
                exposed_ids.update(c["scenario_id"] for c in previous["cases"])
                exposed_inputs.update(c["input_hash"] for c in previous["cases"])
            seed = secrets.token_hex(32)
            rng = random.Random(int(seed, 16))
            selected = []
            for requirement in requirements:
                available = [c for c in candidates if c["stratum_id"] == requirement["stratum_id"]
                             and c["scenario_id"] not in exposed_ids and c["input_hash"] not in exposed_inputs]
                count = requirement["count"]
                if len(available) < count:
                    raise InsufficientScenarios(
                        f"stratum {requirement['stratum_id']} requires {count} fresh scenarios; {len(available)} available"
                    )
                selected.extend(rng.sample(available, count))
            rng.shuffle(selected)
            manifest = {"form_version": FORM_VERSION, "form_id": form_id,
                        "product": product, "scope_hash": scope_hash,
                        "policy": policy, "policy_hash": policy_hash,
                        "selection_seed": seed, "excluded_prior_form_ids": prior_form_ids,
                        "cases": selected, "test_count": len(selected),
                        "counts_as_additional_unique_tests": 0}
            encoded = _json(manifest)
            content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            conn.execute(
                "INSERT INTO variable_test_forms(form_id, scope_hash, product, content_hash, manifest) VALUES(?,?,?,?,?)",
                (form_id, scope_hash, product, content_hash, encoded),
            )
            return json.loads(encoded)

    @staticmethod
    def report_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
        """Only aggregate coverage and hashes may leave the private store."""
        return {
            "form_version": manifest["form_version"], "form_id": manifest["form_id"],
            "product": manifest["product"], "test_count": manifest["test_count"],
            "bank_version": manifest["policy"]["bank_version"],
            "policy_hash": manifest["policy_hash"],
            "form_hash": _digest(dict(manifest)),
            "coverage": manifest["policy"]["strata"],
        }
