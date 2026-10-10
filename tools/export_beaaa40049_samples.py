"""Export reviewer samples for BEAAA-40049; generated files are deterministic."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanners.full_scan_injection_wave2 import language_cases

OUT = ROOT / "artifacts" / "beaaa-40049" / "language-samples"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for language in ("es", "fr", "de", "pt", "ar"):
        path = OUT / f"{language}-50.jsonl"
        path.write_text(
            "".join(json.dumps(asdict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in language_cases(language)),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
