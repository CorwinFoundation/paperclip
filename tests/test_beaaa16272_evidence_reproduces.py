"""The committed lane-C evidence corpus must re-emit byte-identically (BEAAA-29808).

artifacts/beaaa-16272/ is generated output, not captured evidence, and nothing
else in the tree reads it. That combination let it drift silently: the
BEAAA-29788 evidence-shape change and the BEAAA-29743 applicability gate both
landed without the committed fixtures moving, so they declared a
scanner_version they would no longer re-emit. This test closes that gap -- the
next shape change either regenerates the corpus or goes red here.
"""
from __future__ import annotations

import filecmp
from pathlib import Path

from evals.aiuc_path_b import emit_evidence

COMMITTED = Path(__file__).resolve().parents[1] / "artifacts" / "beaaa-16272"


def _relative_json(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*.json")}


def test_committed_lane_c_evidence_reemits_byte_identically(tmp_path: Path) -> None:
    emit_evidence.emit(tmp_path)

    committed = _relative_json(COMMITTED)
    regenerated = _relative_json(tmp_path)
    assert committed == regenerated, {
        "only_committed": sorted(committed - regenerated),
        "only_regenerated": sorted(regenerated - committed),
    }

    differing = [
        name
        for name in sorted(committed)
        if not filecmp.cmp(COMMITTED / name, tmp_path / name, shallow=False)
    ]
    assert differing == []


def test_every_manifest_family_is_exercised_not_skipped(tmp_path: Path) -> None:
    """A gated family must declare its flag, or the corpus proves nothing."""
    import json

    from evals.aiuc_path_b import manifest

    emit_evidence.emit(tmp_path)
    families = {e["family"] for e in manifest.full_scan_manifest()["families"]}
    seen: set[str] = set()
    for path in (tmp_path / "evidence").rglob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["applicable"] is True, (str(path), payload["applicability_reason"])
        seen.add(path.parent.name)
    assert seen == families
