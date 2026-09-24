from __future__ import annotations

import argparse
import json
from pathlib import Path

from .approval import approve_mandate, build_issuer
from .core import EvidenceStore, Mandate, SyntheticMerchantAdapter
from .path1 import run_path1_cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Run five deterministic Stage A synthetic cases")
    parser.add_argument("--db", default="stage-a-demo.sqlite3")
    args = parser.parse_args()
    path = Path(args.db)
    store = EvidenceStore(path)
    # Start-up wiring: the signing capability is built here, once, from an
    # injected secret, and never inside the per-case execution loop below.
    human = build_issuer()
    merchant = SyntheticMerchantAdapter()
    cases = [
        ("correct", Mandate("m-correct", "SKU-1", 1200), "SKU-1", 1200, True, "idem-correct"),
        # Within the mandate: the agent spent less than it was allowed to. Reviewable, not refused.
        ("mismatch", Mandate("m-mismatch", "SKU-2", 2500), "SKU-2", 2400, True, "idem-mismatch"),
        ("duplicate", Mandate("m-duplicate", "SKU-3", 900), "SKU-3", 900, True, "idem-duplicate"),
        ("incomplete", Mandate("m-incomplete", "SKU-4", 400), "SKU-4", 400, False, "idem-incomplete"),
        ("reconstruct", Mandate("m-reconstruct", "SKU-5", 5100), "SKU-5", 5100, True, "idem-reconstruct"),
    ]
    packets = []
    for case_id, mandate, sku, amount, has_evidence, idem in cases:
        # Human step: mint and record one grant. Only its id reaches execution.
        grant = approve_mandate(store, case_id=case_id, mandate=mandate, issuer=human)
        packets.append(store.record_case(case_id=case_id, grant_id=grant,
            asserted_sku=sku, asserted_amount_cents=amount, idempotency_key=idem,
            merchant=merchant, independent_evidence=has_evidence))
        if case_id == "duplicate":
            # An approved retry: a fresh grant, the same idempotency key.
            grant = approve_mandate(store, case_id=case_id, mandate=mandate, issuer=human)
            store.record_case(case_id=case_id, grant_id=grant,
                asserted_sku=sku, asserted_amount_cents=amount, idempotency_key=idem,
                merchant=merchant, independent_evidence=has_evidence)
    store.close()
    reopened = EvidenceStore(path)
    output = {"mode": "DEMO", "database": str(path), "cases": [reopened.packet(p["case_id"]) for p in packets],
              "duplicate_authoritative_executions": reopened.execution_count("idem-duplicate")}
    reopened.close()
    # Path 1: BackBond observes and records the user's approval. Includes the
    # two non-recoverable-loss cases from the Founder ruling, section 2.
    output["path1_cases"] = run_path1_cases(path.with_name(path.name + ".path1"))
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
