#!/usr/bin/env python3
"""Score a pinned, probe-keyed operator transcript (BEAAA-339).

This adapter is deliberately upstream of Scanner scoring.  It validates the
operator envelope and emits replay measurements; it never changes rubric or
corpus state.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.bench import citation_integrity_legal, p1_3_consistency_golden
from harness.runner import AnthropicRunner, ModelRunner, OpenAIRunner

SCHEMA = "are-scanner.operator-transcript.v1"
MAX_BYTES = 10 * 1024 * 1024
ALLOWED_DIMS = {"P1.1", "P1.2", "P1.3", "P1.5", "P2.1", "P3.1", "P3.2", "P3.3"}
REQUIRED_TOP = {"schema", "scanner_version", "corpus_version", "model_versions", "seed", "responses"}


class InvalidTranscript(ValueError):
    pass


def _need(value: Any, kind: type, where: str) -> None:
    if not isinstance(value, kind) or (kind is str and not value.strip()):
        raise InvalidTranscript(f"{where} must be {kind.__name__}")


def load_transcript(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_BYTES:
        raise InvalidTranscript("transcript exceeds 10 MB")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidTranscript(f"cannot read JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidTranscript("top level must be object")
    missing = sorted(REQUIRED_TOP - data.keys())
    if missing:
        raise InvalidTranscript(f"missing required fields: {', '.join(missing)}")
    if data["schema"] != SCHEMA:
        raise InvalidTranscript(f"schema must equal {SCHEMA}")
    _need(data["scanner_version"], str, "scanner_version")
    _need(data["corpus_version"], str, "corpus_version")
    _need(data["model_versions"], dict, "model_versions")
    if not isinstance(data["seed"], int) or isinstance(data["seed"], bool):
        raise InvalidTranscript("seed must be int")
    responses = data["responses"]
    if not isinstance(responses, list) or not responses:
        raise InvalidTranscript("responses must be a non-empty array")
    seen: set[tuple[str, str]] = set()
    for i, row in enumerate(responses):
        where = f"responses[{i}]"
        _need(row, dict, where)
        for field in ("turn_index", "dim", "probe_id", "prompt", "content"):
            if field not in row:
                raise InvalidTranscript(f"{where}.{field} is required")
        if not isinstance(row["turn_index"], int) or isinstance(row["turn_index"], bool):
            raise InvalidTranscript(f"{where}.turn_index must be int")
        for field in ("dim", "probe_id", "prompt", "content"):
            _need(row[field], str, f"{where}.{field}")
        if row["dim"] not in ALLOWED_DIMS:
            raise InvalidTranscript(f"{where}.dim must be one of {sorted(ALLOWED_DIMS)}")
        key = (row["dim"], row["probe_id"])
        if key in seen:
            raise InvalidTranscript(f"duplicate dim/probe_id: {key[0]}/{key[1]}")
        seen.add(key)
        variants = row.get("variant_responses")
        if variants is not None:
            if row["dim"] != "P1.3" or not isinstance(variants, list) or not variants:
                raise InvalidTranscript(f"{where}.variant_responses is only valid as a non-empty P1.3 array")
            variant_keys: set[tuple[str, float]] = set()
            for j, variant in enumerate(variants):
                vwhere = f"{where}.variant_responses[{j}]"
                _need(variant, dict, vwhere)
                if set(("prompt", "temperature", "content")) - variant.keys():
                    raise InvalidTranscript(f"{vwhere} requires prompt, temperature, content")
                _need(variant["prompt"], str, f"{vwhere}.prompt")
                _need(variant["content"], str, f"{vwhere}.content")
                temp = variant["temperature"]
                if isinstance(temp, bool) or not isinstance(temp, (int, float)) or float(temp) not in (0.0, 0.3, 0.7):
                    raise InvalidTranscript(f"{vwhere}.temperature must be 0.0, 0.3, or 0.7")
                vkey = (variant["prompt"].strip(), float(temp))
                if vkey in variant_keys:
                    raise InvalidTranscript(f"duplicate P1.3 prompt/temperature at {vwhere}")
                variant_keys.add(vkey)
        elif row["dim"] == "P1.3":
            raise InvalidTranscript(f"{where}.variant_responses is required for P1.3")
    return data


class _ReplayRunner(ModelRunner):
    """Serve captured subject responses to an unchanged pinned bench."""

    def __init__(self, model: str, responses: dict[str, str]):
        self._model = model
        self._responses = responses

    def model_id(self) -> str:
        return self._model

    def complete(self, prompt: str) -> str:
        try:
            return self._responses[prompt]
        except KeyError as exc:
            raise InvalidTranscript("bench requested a prompt absent from transcript") from exc


def _model_version(data: dict[str, Any], name: str) -> str:
    value = data["model_versions"].get(name)
    if not isinstance(value, str) or not value.strip():
        raise InvalidTranscript(f"model_versions.{name} must be str")
    return value


def _build_judge(data: dict[str, Any]) -> ModelRunner:
    pinned = _model_version(data, "judge")
    provider, sep, model = pinned.partition(":")
    if not sep or not model:
        raise InvalidTranscript("model_versions.judge must be provider:model")
    if provider == "anthropic":
        return AnthropicRunner(model, max_tokens=256, temperature=0.0)
    if provider == "openai":
        return OpenAIRunner(model, max_tokens=256, temperature=0.0, seed=data["seed"])
    raise InvalidTranscript("model_versions.judge provider must be anthropic or openai")


def _score_p12(data: dict[str, Any], rows: list[dict[str, Any]], judge: ModelRunner) -> dict[str, Any]:
    seeds = citation_integrity_legal.load_seeds(
        REPO / "rubric/probe_mappings/citation_integrity_legal.yaml"
    )
    by_id = {row["probe_id"]: row for row in rows}
    selected = [seed for seed in seeds if seed.id in by_id]
    if len(selected) != len(rows):
        unknown = sorted(set(by_id) - {seed.id for seed in seeds})
        raise InvalidTranscript(f"P1.2 unknown canonical probe_id(s): {', '.join(unknown)}")
    for seed in selected:
        if by_id[seed.id]["prompt"] != seed.prompt:
            raise InvalidTranscript(f"P1.2 prompt mismatch for {seed.id}")
    subject = _ReplayRunner(
        _model_version(data, "subject"),
        {seed.prompt: by_id[seed.id]["content"] for seed in selected},
    )
    return citation_integrity_legal.run(subject, judge, selected)


def _score_p13(data: dict[str, Any], rows: list[dict[str, Any]], judge: ModelRunner) -> dict[str, Any]:
    probes = p1_3_consistency_golden.load_probes(
        REPO / "rubric/probe_mappings/p1_3_consistency_golden.yaml"
    )
    canonical = {probe.id: probe for probe in probes}
    by_id = {row["probe_id"]: row for row in rows}
    unknown = sorted(set(by_id) - set(canonical))
    if unknown:
        raise InvalidTranscript(f"P1.3 unknown canonical probe_id(s): {', '.join(unknown)}")

    responses: dict[str, str] = {}
    selected = []
    for probe in probes:
        row = by_id.get(probe.id)
        if row is None:
            continue
        if row["prompt"] != probe.prompt_a:
            raise InvalidTranscript(f"P1.3 prompt mismatch for {probe.id}")
        variants = {
            (variant["prompt"], float(variant["temperature"])): variant["content"]
            for variant in row["variant_responses"]
        }
        expected = {
            (probe.prompt_a, 0.3), (probe.prompt_a, 0.7),
            (probe.prompt_b, 0.0), (probe.prompt_b, 0.3), (probe.prompt_b, 0.7),
        }
        if set(variants) != expected:
            raise InvalidTranscript(
                f"P1.3 variant prompt/temperature matrix mismatch for {probe.id}"
            )
        responses[probe.prompt_a] = row["content"]
        responses[probe.prompt_b] = variants[(probe.prompt_b, 0.0)]
        selected.append(probe)

    subject = _ReplayRunner(_model_version(data, "subject"), responses)
    return p1_3_consistency_golden.run(
        subject,
        judge,
        selected,
        scanner_version=data["scanner_version"],
        corpus_version=data["corpus_version"],
        seed=data["seed"],
    )


def _raw(row: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
    dim = row["dim"]
    if dim == "P1.5":
        import re
        match = re.search(r"(?:^|\n)\s*(0(?:\.\d+)?|1(?:\.0+)?)\s*$", row["content"].strip())
        confidence = float(match.group(1)) if match else None
        return None, {"confidence": confidence, "coverage_label": "replayed" if confidence is not None else "coverage-not-scored"}
    if dim == "P2.1":
        answer = row["content"].strip()[:1]
        parsed = int(answer) if answer in "012" else None
        return None, {"selected_answer": parsed, "coverage_label": "replayed" if parsed is not None else "coverage-not-scored"}
    # Existing single-response dimensions require their pinned bench/judge for a
    # raw.  Preserve the replay row without fabricating a score.
    return None, {"coverage_label": "replay-ready"}


def score(data: dict[str, Any], out_dir: Path, *, judge: ModelRunner | None = None) -> dict[str, Any]:
    measurements = []
    p12_rows = [row for row in data["responses"] if row["dim"] == "P1.2"]
    p13_rows = [row for row in data["responses"] if row["dim"] == "P1.3"]
    live_judge = judge
    if p12_rows or p13_rows:
        live_judge = live_judge or _build_judge(data)
    if p12_rows:
        p12 = _score_p12(data, p12_rows, live_judge)
        measurements.append(p12)
        (out_dir / "measurements").mkdir(parents=True, exist_ok=True)
        (out_dir / "measurements/P1.2.json").write_text(
            json.dumps(p12, indent=2, sort_keys=True) + "\n"
        )
    if p13_rows:
        p13 = _score_p13(data, p13_rows, live_judge)
        measurements.append(p13)
        (out_dir / "measurements").mkdir(parents=True, exist_ok=True)
        (out_dir / "measurements/P1.3.json").write_text(
            json.dumps(p13, indent=2, sort_keys=True) + "\n"
        )
    for row in data["responses"]:
        if row["dim"] in ("P1.2", "P1.3"):
            continue
        raw, detail = _raw(row)
        measurements.append({"dim_id": row["dim"], "probe_id": row["probe_id"], "raw": raw, **detail})
    report = {
        "schema": SCHEMA,
        "status": "v0.2-default",
        "scanner_version": data["scanner_version"],
        "corpus_version": data["corpus_version"],
        "model_versions": data["model_versions"],
        "seed": data["seed"],
        "measurements": measurements,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "transcript_score.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript-from-json", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        data = load_transcript(args.transcript_from_json)
        score(data, args.out_dir)
    except (InvalidTranscript, OSError) as exc:
        print(f"invalid transcript: {exc}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
