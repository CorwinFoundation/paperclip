import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from scripts.deploy.retained_evidence_export import EXPECTED_ROWS, build_archive


def _request(root: Path) -> dict:
    rows = []
    for index in range(EXPECTED_ROWS):
        data = json.dumps({"response": index}, sort_keys=True).encode()
        relative = f"run/row-{index:03d}.json"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        rows.append({
            "row_id": f"rank2-{index:03d}",
            "relative_path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
            "provenance": {"candidate": "acca132dbb4afc542476b588604f22f2312ae9e2", "ordinal": index},
        })
    return {"request_id": "beaaa-31116-rank2", "rows": rows}


def test_exact_roster_export_is_machine_readable_and_repeatable(tmp_path: Path) -> None:
    request = _request(tmp_path)
    first = build_archive(request, tmp_path)
    second = build_archive(request, tmp_path)
    assert first == second

    with tarfile.open(fileobj=io.BytesIO(first), mode="r:") as archive:
        names = archive.getnames()
        manifest = json.load(archive.extractfile("manifest.json"))
    assert len(names) == EXPECTED_ROWS + 1
    assert manifest["row_count"] == EXPECTED_ROWS
    assert len(manifest["rows"]) == EXPECTED_ROWS
    assert all(row["provenance"] for row in manifest["rows"])


def test_export_fails_closed_on_wrong_count_hash_and_path_escape(tmp_path: Path) -> None:
    request = _request(tmp_path)
    with pytest.raises(ValueError, match="exactly 187"):
        build_archive({**request, "rows": request["rows"][:-1]}, tmp_path)

    request["rows"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="sha256 mismatch"):
        build_archive(request, tmp_path)

    request = _request(tmp_path)
    outside = tmp_path.parent / "outside.json"
    outside.write_text("{}")
    request["rows"][0]["relative_path"] = "../outside.json"
    request["rows"][0]["sha256"] = hashlib.sha256(b"{}").hexdigest()
    with pytest.raises(ValueError, match="escapes evidence root"):
        build_archive(request, tmp_path)
