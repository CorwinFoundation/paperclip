from pathlib import Path

import yaml

from are_probe.cli import EXIT_CONFIG, main
from fixture_target import serve_in_thread


SDK_ROOT = Path(__file__).resolve().parents[1]


def test_pre_bind_placeholders_fail_before_scanner_invocation(tmp_path):
    srv, url = serve_in_thread(0)
    try:
        cfg = yaml.safe_load(
            (SDK_ROOT / "config" / "are-probe.yaml.example").read_text()
        )
        cfg["target"]["url"] = url
        cfg["probes"] = [{
            "family": "refusal_robustness",
            "corpus_pack": "refusal_robustness_v0.1",
        }]
        cfg["scanner_bin_default"] = "/bin/false"
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg))

        rc = main([
            "--config", str(cfg_path),
            "--out", str(tmp_path / "envelope.json"),
            "--agent", "placeholder-gate-fixture",
        ])
    finally:
        srv.shutdown()

    assert rc == EXIT_CONFIG
    assert not (tmp_path / "envelope.json").exists()
