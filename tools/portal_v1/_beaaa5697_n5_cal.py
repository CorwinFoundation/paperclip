#!/usr/bin/env python3
"""BEAAA-5697 N=5 calibration receipt generator.

Auth-gate-only change (BEAAA-4996 admin-gate idiom replicated at portal.py:2330
for /scans + portal.py:2335 dp-bearer for /dp/{id}/scans per concurrent BEAAA-5698
implementation). No rubric / scoring / contract-shape touched.

Receipt shape: {seed -> {are_total, band, confidence, subscores, envelopes_stored,
  scan_status}}. Empirical Δ_total = max - min across seeds; expect 0 by construction.

Output: artifacts/beaaa5697_n5_calibration.json
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connector_sim import ConnectorSimConfig, run_connector_flow
from portal import PortalHandler, build_service


def run_one(seed: int) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"beaaa5697_n5_seed{seed}_") as tmp:
        service = build_service(pathlib.Path(tmp))
        PortalHandler.service = service
        server = ThreadingHTTPServer(("127.0.0.1", 0), PortalHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            invite = service.create_invite(
                f"N5 DP seed{seed}", f"owner-seed{seed}@example.invalid", "call-center"
            )
            result = run_connector_flow(
                ConnectorSimConfig(
                    base_url=f"http://127.0.0.1:{server.server_port}",
                    invite_token=invite["invite_token"],
                    dp_org_name=f"N5 DP seed{seed}",
                    endpoint_url=f"https://staging.example.invalid/seed{seed}/agent",
                    cert_ref=f"n5-seed{seed}-cert-ref",
                )
            )
            row = service.db.execute(
                "SELECT status, score_json FROM scans WHERE id = ?",
                (result["scan_id"],),
            ).fetchone()
            score = json.loads(row["score_json"]) if row["score_json"] else {}
            return {
                "seed": seed,
                "scan_status": row["status"],
                "envelopes_stored": result["submitted_envelopes"],
                "are_total": score.get("are_total"),
                "band": score.get("band"),
                "confidence": score.get("confidence"),
                "subscores": score.get("subscores"),
                "scanner_version": score.get("scanner_version"),
                "corpus_version": score.get("corpus_version"),
            }
        finally:
            server.shutdown()
            server.server_close()
            service.close()


def main() -> int:
    rows = [run_one(s) for s in range(5)]
    are_totals = [r["are_total"] for r in rows]
    delta_total = (max(are_totals) - min(are_totals)) if all(t is not None for t in are_totals) else None
    bands = sorted({r["band"] for r in rows})
    pillar_keys = sorted({k for r in rows for k in (r["subscores"] or {}).keys()})
    delta_pillar = {
        k: max(r["subscores"][k] for r in rows) - min(r["subscores"][k] for r in rows)
        for k in pillar_keys
    }
    receipt = {
        "ticket": "BEAAA-5697",
        "parent_ticket": "BEAAA-5675",
        "source_findings": ["BEAAA-5676 F-01", "BEAAA-5676 F-02"],
        "classification": "rule-execution (auth-gate only; subsumed by BEAAA-5698 broader DP-bearer scope)",
        "scope": "no rubric / scoring / contract-shape change",
        "ran_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n": len(rows),
        "rows": rows,
        "delta_are_total": delta_total,
        "delta_per_pillar": delta_pillar,
        "bands_observed": bands,
        "calibration_pass": delta_total == 0
        and all(v == 0 for v in delta_pillar.values())
        and len(bands) == 1,
        "scanner_version_at_run": rows[0]["scanner_version"] if rows else None,
        "corpus_version_at_run": rows[0]["corpus_version"] if rows else None,
        "by_construction_argument": (
            "BEAAA-5697 + BEAAA-5698 changes touch only auth gates "
            "(portal.py:2328-2336 dispatcher + portal.py:2165 _require_dp_bearer + "
            "portal.py:624-652 accept_invite dp_bearer issuance + portal.py:469-470 "
            "design_partners.dp_bearer_hash additive column). _fixture_score "
            "(portal.py:997-1025) untouched; envelope/score-path code untouched; "
            "score depends on 10 envelopes per scan with verdict=partial weight=1, "
            "which is deterministic for the same fixture. Δ_total = 0 by construction."
        ),
    }
    out = pathlib.Path(__file__).parent / "artifacts" / "beaaa5697_n5_calibration.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(
        {
            "calibration_pass": receipt["calibration_pass"],
            "delta_are_total": receipt["delta_are_total"],
            "delta_per_pillar": receipt["delta_per_pillar"],
            "bands_observed": receipt["bands_observed"],
            "n": receipt["n"],
            "receipt_path": str(out.relative_to(pathlib.Path(__file__).parent.parent.parent)),
        },
        indent=2,
    ))
    return 0 if receipt["calibration_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
