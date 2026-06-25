"""are-probe CLI entrypoint.

End-to-end: load config -> verify corpus SHA -> run probes against target
-> grade -> build eval_results dir -> invoke bin/are-scan -> assemble
v1.0 envelope -> optionally sign -> write envelope to --out.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .canonical import canonical_json
from .config import ConfigError, load_config
from .corpus import CorpusError, load_lockfile, load_pack
from .envelope import EnvelopeError, build_envelope
from .eval_results_builder import build_eval_results, write_eval_results_dir
from .probes import FAMILY_GRADERS
from .scan_wrapper import ScanError, run_master_scan
from .signer import sign_envelope
from .target import call_target

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_CORPUS = 3
EXIT_TARGET = 4
EXIT_SCAN = 5
EXIT_ENVELOPE = 6


def _sdk_root() -> Path:
    # cli.py lives at engineering/probe-sdk/src/are_probe/cli.py; SDK root
    # is two parents up.
    return Path(__file__).resolve().parents[2]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="are-probe",
        description=(
            "ARE Probe SDK R1 (internal, shelf-only). Runs a deterministic "
            "probe sequence against a target agent's HTTP surface and emits "
            "an envelope per contracts/are-scanner-api-v1.md@v1.0."
        ),
    )
    p.add_argument("--config", required=True,
                   help="path to are-probe.yaml")
    p.add_argument("--target", default=None,
                   help="override config target.url (useful for fixture tests)")
    p.add_argument("--agent", default="are-probe-r1-target",
                   help="agent_id stamped into envelope (default: are-probe-r1-target)")
    p.add_argument("--out", default=None,
                   help="envelope output path; overrides config.output.envelope_path")
    p.add_argument("--dp-attestations", default=None,
                   help="optional DP attestations JSON for unprobed dims")
    p.add_argument("--freeze-timestamp", action="store_true",
                   help="force run_timestamp=1970-01-01T00:00:00Z (used by N=5 tests)")
    p.add_argument("--scanner-bin", default=None,
                   help="override scanner_bin from config / env")
    return p


def _load_dp_attestations(path: str | None) -> dict | None:
    if not path:
        return None
    return json.loads(Path(path).read_text())


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"are-probe: config error: {e}", file=sys.stderr)
        return EXIT_CONFIG

    target_url = args.target or cfg.target.url
    scanner_bin = args.scanner_bin or cfg.scanner_bin
    out_path = Path(args.out or cfg.output.envelope_path).expanduser().resolve()
    freeze_ts = bool(args.freeze_timestamp or cfg.output.freeze_timestamp)
    dp_att = _load_dp_attestations(args.dp_attestations)

    sdk_root = _sdk_root()
    try:
        lockfile = load_lockfile(sdk_root / "lockfile.toml")
    except Exception as e:
        print(f"are-probe: lockfile load failed: {e}", file=sys.stderr)
        return EXIT_CORPUS

    measured: dict[str, tuple[float, str]] = {}
    family_results = []
    primary_pack = None

    for fam in cfg.probes:
        try:
            pack = load_pack(sdk_root, fam.corpus_pack, lockfile)
        except CorpusError as e:
            print(f"are-probe: corpus error: {e}", file=sys.stderr)
            return EXIT_CORPUS
        if pack.corpus_version != cfg.corpus_version:
            print(
                f"are-probe: corpus version mismatch for {fam.corpus_pack!r}: "
                f"pack={pack.corpus_version} cfg={cfg.corpus_version}",
                file=sys.stderr,
            )
            return EXIT_CORPUS
        if pack.probe_set_version != cfg.probe_set_version:
            print(
                f"are-probe: probe set version mismatch for {fam.corpus_pack!r}: "
                f"pack={pack.probe_set_version} cfg={cfg.probe_set_version}",
                file=sys.stderr,
            )
            return EXIT_CORPUS
        primary_pack = pack  # last pack wins for envelope metadata; R1 = 1 pack
        responses = []
        for probe in pack.probes:
            try:
                r = call_target(
                    url=target_url,
                    shape=cfg.target.shape,
                    auth_mode=cfg.target.auth_mode,
                    bearer_env_var=cfg.target.bearer_env_var,
                    timeout_seconds=cfg.target.timeout_seconds,
                    probe_id=probe.probe_id,
                    prompt=probe.prompt,
                    response_json_paths=cfg.target.response_json_paths,
                )
            except Exception as e:
                print(f"are-probe: target call failed for {probe.probe_id}: {e}",
                      file=sys.stderr)
                return EXIT_TARGET
            responses.append(r)
        grader = FAMILY_GRADERS.get(fam.family)
        if grader is None:
            print(f"are-probe: unknown probe family {fam.family!r}", file=sys.stderr)
            return EXIT_CONFIG
        result = grader(pack.probes, responses)
        family_results.append(result)
        evidence_uri = (
            f"are-probe://family/{fam.family}/"
            f"{pack.pack_id}/sha256:{pack.sha256[:16]}"
        )
        measured[result.rubric_point_id] = (result.raw, evidence_uri)

    eval_results = build_eval_results(measured=measured, dp_attestations=dp_att)

    with tempfile.TemporaryDirectory(prefix="are-probe-input-") as td:
        td_path = Path(td)
        write_eval_results_dir(
            target_dir=td_path,
            eval_results=eval_results,
            model_versions=cfg.model_versions,
        )
        try:
            scan = run_master_scan(
                scanner_bin=scanner_bin,
                agent_id=args.agent,
                input_dir=td_path,
                seed=cfg.seed,
                corpus_version=cfg.corpus_version,
                scanner_version=cfg.scanner_version,
            )
        except ScanError as e:
            print(f"are-probe: scan failed: {e}", file=sys.stderr)
            return EXIT_SCAN

    try:
        envelope = build_envelope(
            contract_output=scan.contract_output,
            agent_id=args.agent,
            config=cfg,
            corpus_pack=primary_pack,
            eval_results=eval_results,
            family_results=family_results,
            freeze_timestamp=freeze_ts,
        )
    except EnvelopeError as e:
        print(f"are-probe: envelope assembly failed: {e}", file=sys.stderr)
        return EXIT_ENVELOPE

    if cfg.signing.sign:
        sig = sign_envelope(envelope, cfg.signing.key_path)
        envelope["signature"] = {
            "key_id": sig.key_id,
            "algorithm": sig.algorithm,
            "public_key_b64url": sig.public_key_b64url,
            "public_key_sha256_fingerprint": sig.public_key_sha256_fingerprint,
            "payload_sha256": sig.payload_sha256,
            "signature_b64url": sig.signature_b64url,
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(canonical_json(envelope) + b"\n")
    print(f"are-probe: wrote {out_path} "
          f"(are_total={envelope['are_total']}, band={envelope['band']})")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
