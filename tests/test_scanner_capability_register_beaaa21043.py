import json
from pathlib import Path
import pytest
from tools.scanner_capability_register import eligible, render

ROOT=Path(__file__).parents[1]
def source(): return json.loads((ROOT/"config/scanner_capability_register.v1.json").read_text())
def test_views_share_receipt(tmp_path):
    receipt=render(source(),tmp_path)
    rid=receipt["register_receipt_id"]
    assert json.loads((tmp_path/"dictionary_records.json").read_text())["register_receipt_id"]==rid
    assert json.loads((tmp_path/"marketing_claims.json").read_text())["register_receipt_id"]==rid
    assert rid in (tmp_path/"executive_summary.md").read_text()
    assert rid in (tmp_path/"inactive_backlog.csv").read_text()
def test_manifest_rule_is_fail_closed():
    assert eligible({"expected":3,"attempted":3,"terminal":3,"evidenced":3,"signature_ref":"sig:x"})
    assert not eligible({"expected":3,"attempted":3,"terminal":2,"evidenced":3,"signature_ref":"sig:x"})
    assert not eligible({"expected":3,"attempted":3,"terminal":3,"evidenced":3})
def test_historical_1180_is_rejected():
    row=next(r for r in source()["records"] if r["count"]==1180)
    assert row["external_claim_state"]=="rejected"
