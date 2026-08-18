"""Corpus pin reader shared by the golden-set bench adapters — BEAAA-23968.

`corpus_version` and `probe_set_version` are contract output fields under
contracts/are-scanner-api-v1.md@v1.3.0: they answer "which corpus produced this
score". Restating the pin as a literal in each runner lets it drift away from
the corpus it labels, which is what BEAAA-22121 did to P1.1 and P1.4. The pin
is read from `reproducibility.probe_set_version` in the loaded probe map so a
corpus bump carries the label with it.

One module, no per-family copy of the same read. P1.1 and P1.4 came in with
BEAAA-23968; P1.3 and P1.5 followed under BEAAA-24002 so no golden family is
left restating its own pin.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_probe_set_version(yaml_path: str | Path) -> str:
    """Return `reproducibility.probe_set_version` from a probe-mapping YAML.

    Fails closed: a map without a usable pin raises rather than letting an
    envelope go out with a corpus_version that identifies nothing.
    """
    data = yaml.safe_load(Path(yaml_path).read_text())
    repro = (data or {}).get("reproducibility") or {}
    pin = repro.get("probe_set_version")
    if not isinstance(pin, str) or not pin.strip():
        raise ValueError(
            f"{yaml_path}: reproducibility.probe_set_version is missing or blank; "
            "refusing to emit an unpinned corpus_version"
        )
    return pin.strip()


def benchmark_label(probe_set_version: str) -> str:
    """`p1_1_factuality_golden@0.2.0` -> `p1_1_factuality_golden-v0.2.0`.

    Keeps the envelope's `benchmark` string on the same pin as
    `probe_set_version` instead of freezing it at its own literal.
    """
    name, sep, version = probe_set_version.partition("@")
    return f"{name}-v{version}" if sep and version else name
