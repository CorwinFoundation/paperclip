"""Build immutable OSS-003 local records from the pinned InjecAgent sources."""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

from evals.oss_pilots.injecagent import SOURCE_COMMIT, canonical_bytes, sha256

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "evals" / "oss_pilots" / "records" / "injecagent_v1.json"
INDICES = (0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360,
           390, 420, 450, 480, 1, 31, 61, 91, 121, 151, 181, 211)
SOURCES = {
    "direct_harm": (
        "data/test_cases_dh_base.json",
        "0a8186468d21389af432e8c7b399ae42264d1b93a07b65c7a489468508604305",
    ),
    "data_stealing": (
        "data/test_cases_ds_base.json",
        "4daab35c62a3845e8b9400f4dca58b9c9f37e57cd33b2337552557fbb26282e9",
    ),
}


def fetch(path: str, expected_sha256: str) -> list[dict[str, Any]]:
    url = f"https://raw.githubusercontent.com/uiuc-kang-lab/InjecAgent/{SOURCE_COMMIT}/{path}"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError(f"source integrity mismatch: {path}")
    return json.loads(payload)


def make_record(kind: str, source_path: str, index: int, row: dict[str, Any]) -> dict[str, Any]:
    prefix = "dh" if kind == "direct_harm" else "ds"
    record = {
        "local_case_id": f"oss.injecagent.{prefix}.{index:03d}.v1",
        "case_kind": kind,
        "source_path": source_path,
        "source_case_index": index,
        "source_row_sha256": sha256(row),
        "user_instruction": row["User Instruction"],
        "user_tool": row["User Tool"],
        "poisoned_tool_response": row["Tool Response"],
        "attacker_instruction": row["Attacker Instruction"],
        "attacker_tools": row["Attacker Tools"],
        "execution_attacker_tools": row["Attacker Tools"][: (2 if kind == "data_stealing" else 1)],
        "attack_identity_sha256": sha256(
            {
                "attacker_instruction": row["Attacker Instruction"],
                "attacker_tools": row["Attacker Tools"],
            }
        ),
        "attack_type": row["Attack Type"],
        "manual_review_label": "attack_success",
    }
    record["record_sha256"] = sha256(record)
    return record


def benign_control(record: dict[str, Any], ordinal: int) -> dict[str, Any]:
    control = {
        "local_case_id": f"oss.injecagent.benign.{ordinal:03d}.v1",
        "case_kind": "benign_control",
        "source_path": record["source_path"],
        "source_case_index": record["source_case_index"],
        "source_row_sha256": record["source_row_sha256"],
        "control_transform": "replace_poisoned_tool_response_with_fixed_benign_payload_v1",
        "user_instruction": record["user_instruction"],
        "user_tool": record["user_tool"],
        "poisoned_tool_response": json.dumps(
            {"fixture": "benign_control", "content": "Requested information only."},
            sort_keys=True,
        ),
        "attacker_instruction": "",
        "attacker_tools": [],
        "execution_attacker_tools": [],
        "attack_identity_sha256": None,
        "attack_type": "Benign Control",
        "manual_review_label": "benign",
    }
    control["record_sha256"] = sha256(control)
    return control


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    records: list[dict[str, Any]] = []
    for kind, (source_path, digest) in SOURCES.items():
        source = fetch(source_path, digest)
        records.extend(make_record(kind, source_path, index, source[index]) for index in INDICES)
    controls = [benign_control(record, ordinal) for ordinal, record in enumerate(records[:25])]
    document = {
        "schema_version": 1,
        "source_project": "uiuc-kang-lab/InjecAgent",
        "source_commit": SOURCE_COMMIT,
        "source_license": "MIT",
        "selection_indices": list(INDICES),
        "records": records + controls,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(document) + b"\n")
    print(json.dumps({"records": len(document["records"]), "sha256": sha256(document)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
