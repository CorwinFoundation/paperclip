"""Reserve a starter-100@4 form before dispatch (BEAAA-38986).

The orchestrator calls ``reserve_starter_cases`` once per scan, before the
first send. The form id is the scan id, so a retry or resume of the same scan
gets the saved form back instead of a new draw. If the bank changed between
attempts, the stored form is replayed as-is. Nothing here falls back to a
fixed list: a stratum without enough fresh scenarios raises
``InsufficientScenarios`` and the scan does not start.

The SQLite store holds private prompts, canaries and the selection seed. Its
path comes from ``BACKBOND_VARIABLE_FORMS_DB`` and must sit in restricted
evidence storage shared by every worker process.
"""

import os
from pathlib import Path

from scanners.starter_pack_v2 import StarterCaseV2
from scanners.starter_pack_v4 import (
    STARTER_BANK_V4_VERSION,
    case_from_payload,
    starter_v4_scenarios,
    starter_v4_strata,
)
from scanners.variable_test_forms import FormError, FormStore, Scenario, Stratum

STARTER_FORMS_DB_ENV = "BACKBOND_VARIABLE_FORMS_DB"
STARTER_PRODUCT = "starter"


def starter_form_store() -> FormStore:
    path = os.environ.get(STARTER_FORMS_DB_ENV, "").strip()
    if not path:
        # Reuse the existing durable runtime storage directory; every worker
        # shares the same reservation file. Operators can pin the path explicitly.
        log_path = os.environ.get("ARE_SCAN_LOG_PATH", "/var/lib/are-scanner/scan_log.sqlite3")
        path = str(Path(log_path).with_name("variable-forms.sqlite3"))
    return FormStore(Path(path))


def reserve_starter_form(
    store: FormStore, *, scan_id: str, tenant_id: str, agent_id: str
) -> dict:
    scope = (tenant_id, agent_id)
    try:
        return store.get(scan_id, scope=scope, product=STARTER_PRODUCT)
    except FormError as exc:
        if str(exc) != "private form not found for this scope and product":
            raise
    return store.create(
            scope=scope,
            product=STARTER_PRODUCT,
            bank_version=STARTER_BANK_V4_VERSION,
            strata=[Stratum(**s) for s in starter_v4_strata()],
            scenarios=[Scenario(**s) for s in starter_v4_scenarios()],
            # Thirty scenarios for ten slots leaves spare supply beyond the
            # previous run; using a two-run window would force a three-form cycle.
            history_window=1,
            form_id=scan_id,
        )


def cases_from_form(form: dict) -> tuple[StarterCaseV2, ...]:
    return tuple(case_from_payload(c["payload"]) for c in form["cases"])


def reserve_starter_cases(
    *, scan_id: str, tenant_id: str, agent_id: str, store: FormStore | None = None
) -> tuple[tuple[StarterCaseV2, ...], dict]:
    """Saved-before-dispatch cases plus the report-safe form summary."""
    store = store or starter_form_store()
    form = reserve_starter_form(store, scan_id=scan_id, tenant_id=tenant_id, agent_id=agent_id)
    return cases_from_form(form), FormStore.report_summary(form)
