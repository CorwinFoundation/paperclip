from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from harness.bench import citation_integrity_legal, p1_3_consistency_golden
from harness.runner import MockRunner
from scripts.score_transcript import InvalidTranscript, load_transcript, score


def _doc() -> dict:
    return {"schema": "are-scanner.operator-transcript.v1", "scanner_version": "0.5.0", "corpus_version": "pinned", "model_versions": {"subject": "fixture", "judge": "pinned"}, "seed": 42, "responses": [{"turn_index": 0, "dim": "P1.5", "probe_id": "cc.1", "prompt": "Question", "content": "Answer\n0.8"}]}


def _write(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "in.json"
    path.write_text(json.dumps(doc))
    return path


def test_v02_default_cli_writes_score_without_experimental(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = subprocess.run([sys.executable, "scripts/score_transcript.py", "--transcript-from-json", str(_write(tmp_path, _doc())), "--out-dir", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads((out / "transcript_score.json").read_text())
    assert report["status"] == "v0.2-default"
    assert report["measurements"][0]["confidence"] == .8


def test_bad_schema_fails_closed(tmp_path: Path) -> None:
    doc = _doc(); doc["schema"] = "wrong"
    try: load_transcript(_write(tmp_path, doc))
    except InvalidTranscript: pass
    else: raise AssertionError("accepted wrong schema")


def test_p13_requires_variants(tmp_path: Path) -> None:
    doc = _doc(); doc["responses"][0]["dim"] = "P1.3"
    try: load_transcript(_write(tmp_path, doc))
    except InvalidTranscript: pass
    else: raise AssertionError("accepted missing variants")


def test_p13_rejects_bad_temperature(tmp_path: Path) -> None:
    doc = _doc(); row = doc["responses"][0]; row["dim"] = "P1.3"; row["variant_responses"] = [{"prompt": "a", "temperature": .5, "content": "x"}]
    try: load_transcript(_write(tmp_path, doc))
    except InvalidTranscript: pass
    else: raise AssertionError("accepted mismatched temperature")


def _p13_row() -> dict:
    probe = p1_3_consistency_golden.load_probes(
        "rubric/probe_mappings/p1_3_consistency_golden.yaml"
    )[0]
    return {
        "turn_index": 0, "dim": "P1.3", "probe_id": probe.id,
        "prompt": probe.prompt_a, "content": "July 20, 1969",
        "variant_responses": [
            {"prompt": probe.prompt_a, "temperature": temp, "content": "July 20, 1969"}
            for temp in (.3, .7)
        ] + [
            {"prompt": probe.prompt_b, "temperature": temp, "content": "July 20, 1969"}
            for temp in (0, .3, .7)
        ],
    }


def test_p13_dispatches_through_canonical_bench(tmp_path: Path) -> None:
    doc = _doc(); doc["responses"] = [_p13_row()]
    judge = MockRunner("consistency-judge", lambda _prompt: "CONSISTENCY: MATCH\nFAILURE_MODE: none\nEVIDENCE_SPAN_A: July 20\nEVIDENCE_SPAN_B: July 20")
    report = score(load_transcript(_write(tmp_path, doc)), tmp_path / "out", judge=judge)
    measurement = report["measurements"][0]
    assert measurement["dim_id"] == "P1.3"
    assert measurement["n_match"] == 1
    assert measurement["raw"] == 4.0
    assert json.loads((tmp_path / "out/measurements/P1.3.json").read_text())["raw"] == 4.0


def test_p13_rejects_tampered_nested_prompt_matrix(tmp_path: Path) -> None:
    doc = _doc(); row = _p13_row(); doc["responses"] = [row]
    row["variant_responses"][-1]["prompt"] = "tampered prompt"
    judge = MockRunner("consistency-judge", lambda _prompt: "CONSISTENCY: MATCH\nFAILURE_MODE: none\nEVIDENCE_SPAN_A:\nEVIDENCE_SPAN_B:")
    try:
        score(load_transcript(_write(tmp_path, doc)), tmp_path / "out", judge=judge)
    except InvalidTranscript as exc:
        assert "matrix mismatch" in str(exc)
    else:
        raise AssertionError("accepted tampered P1.3 prompt matrix")


def test_p21_accepts_replay_shape(tmp_path: Path) -> None:
    doc = _doc(); doc["responses"][0].update(dim="P2.1", content="2")
    assert load_transcript(_write(tmp_path, doc))["responses"][0]["dim"] == "P2.1"


def test_p12_dispatches_through_canonical_bench_and_live_judge(tmp_path: Path) -> None:
    seed = citation_integrity_legal.load_seeds(
        "rubric/probe_mappings/citation_integrity_legal.yaml"
    )[0]
    doc = _doc()
    doc["responses"] = [{
        "turn_index": 0,
        "dim": "P1.2",
        "probe_id": seed.id,
        "prompt": seed.prompt,
        "content": "The rule is stated in 17 U.S.C. § 107.",
    }]
    judge = MockRunner("citation-judge", lambda _prompt: "PLAUSIBILITY: YES\nSEMANTIC: YES")
    report = score(load_transcript(_write(tmp_path, doc)), tmp_path / "out", judge=judge)
    measurement = report["measurements"][0]
    assert measurement["dim_id"] == "P1.2"
    assert measurement["n_with_citation"] == 1
    assert measurement["raw"] == 4.0
    assert json.loads((tmp_path / "out/measurements/P1.2.json").read_text())["raw"] == 4.0


def test_p12_rejects_prompt_that_does_not_match_canonical_fixture(tmp_path: Path) -> None:
    seed = citation_integrity_legal.load_seeds(
        "rubric/probe_mappings/citation_integrity_legal.yaml"
    )[0]
    doc = _doc()
    doc["responses"] = [{
        "turn_index": 0, "dim": "P1.2", "probe_id": seed.id,
        "prompt": "tampered prompt", "content": "17 U.S.C. § 107",
    }]
    judge = MockRunner("citation-judge", lambda _prompt: "PLAUSIBILITY: YES\nSEMANTIC: YES")
    try:
        score(load_transcript(_write(tmp_path, doc)), tmp_path / "out", judge=judge)
    except InvalidTranscript as exc:
        assert "prompt mismatch" in str(exc)
    else:
        raise AssertionError("accepted non-canonical P1.2 prompt")
