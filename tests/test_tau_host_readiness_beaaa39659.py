from pathlib import Path

from scanners.eval_framework_venv import FRAMEWORKS


ROOT = Path(__file__).resolve().parents[1]
TAU = "59a200c6d575d595120f1cb70fea53cef0632f6b"
TAU2 = "5ebebbe827b455b3ed04fcb9294235c6ef4e5fd6"


def test_tau_install_recipe_is_pinned_and_repeatable() -> None:
    pins = (ROOT / "requirements/eval_pins.txt").read_text()
    installer = (ROOT / "scripts/build_eval_framework_venvs.sh").read_text()
    assert f"tau-bench@{TAU}" in pins
    assert f"tau2-bench@{TAU2}" in pins
    assert '[tau_bench]="requirements/eval_pins.txt tau_bench tau-bench ' + TAU in installer
    assert '"$PYTHON" -m venv --clear "$vdir"' in installer
    assert 'import json, pathlib, ${mod}' in installer
    assert FRAMEWORKS["tau_bench"].pinned_version == TAU
