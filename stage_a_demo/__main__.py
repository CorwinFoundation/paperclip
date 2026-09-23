from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import EvidenceStore, HumanApprovalIssuer, Mandate, SyntheticMerchantAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description="Run five deterministic Stage A synthetic cases")
    parser.add_argument("--db", default="stage-a-demo.sqlite3")
    args = parser.parse_args()
    path = Path(args.db)
    store = EvidenceStore(path)
    human = HumanApprovalIssuer()
    merchant = SyntheticMerchantAdapter()
    cases = [
        ("correct", Mandate("m-correct", "SKU-1", 1200), "SKU-1", 1200, True, "idem-correct"),
        ("mismatch", Mandate("m-mismatch", "SKU-2", 2500), "SKU-X", 2600, True, "idem-mismatch"),
        ("duplicate", Mandate("m-duplicate", "SKU-3", 900), "SKU-3", 900, True, "idem-duplicate"),
        ("incomplete", Mandate("m-incomplete", "SKU-4", 400), "SKU-4", 400, False, "idem-incomplete"),
        ("reconstruct", Mandate("m-reconstruct", "SKU-5", 5100), "SKU-5", 5100, True, "idem-reconstruct"),
    ]
    packets = []
    for case_id, mandate, sku, amount, has_evidence, idem in cases:
        token, verifier = human.approve(mandate)
        packets.append(store.record_case(case_id=case_id, mandate=mandate, approval_token=token,
            asserted_sku=sku, asserted_amount_cents=amount, idempotency_key=idem,
            approval_verifier=verifier, merchant=merchant, independent_evidence=has_evidence))
        if case_id == "duplicate":
            store.record_case(case_id=case_id, mandate=mandate, approval_token=token,
                asserted_sku=sku, asserted_amount_cents=amount, idempotency_key=idem,
                approval_verifier=verifier, merchant=merchant, independent_evidence=has_evidence)
    store.close()
    reopened = EvidenceStore(path)
    output = {"mode": "DEMO", "database": str(path), "cases": [reopened.packet(p["case_id"]) for p in packets],
              "duplicate_authoritative_executions": reopened.execution_count("idem-duplicate")}
    reopened.close()
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
