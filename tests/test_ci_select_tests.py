"""Unit tests for scripts/ci_select_tests.py (CI affected-test selection).

The selector must be fail-closed: every ambiguous case below expects ALL.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci_select_tests.py"


def _load():
    spec = importlib.util.spec_from_file_location("ci_select_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


sel = _load()


FIXTURE_FILES: dict[str, str] = {
    "api/__init__.py": "",
    "api/app.py": "from rubric.scoring import score\n\ndef handle():\n    return score()\n",
    "api/orphan.py": "def nobody_tests_me():\n    return 1\n",
    "rubric/__init__.py": "",
    "rubric/scoring.py": "def score():\n    return 42\n",
    "rubric/probe_mappings/garak_beaaa152.yaml": "rows: []\n",
    "scanners/__init__.py": "",
    "scanners/eval_framework_venv.py": "FRAMEWORKS = {}\n",
    "tools/__init__.py": "",
    "tools/validate_garak_registry.py": "def validate(path):\n    return []\n",
    "scripts/helper.py": "print('helper nobody imports or names')\n",
    "tests/__init__.py": "",
    "tests/conftest.py": "import rubric.scoring\n",
    "tests/_helpers.py": "HELP = 1\n",
    "tests/test_app.py": "from api.app import handle\n\ndef test_handle():\n    assert handle() == 42\n",
    "tests/test_scoring.py": "import rubric.scoring as s\n\ndef test_score():\n    assert s.score() == 42\n",
    "tests/test_garak_registry_resolution_beaaa31623.py": (
        "from pathlib import Path\nfrom tools.validate_garak_registry import validate\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "def test_registry():\n"
        "    assert validate(ROOT / 'rubric/probe_mappings/garak_beaaa152.yaml') == []\n"
    ),
    "tests/test_fixture_reader.py": (
        "import json, pathlib\n"
        "def test_fixture():\n"
        "    p = pathlib.Path(__file__).parent / 'fixtures' / 'sample_payload.json'\n"
        "    assert json.loads(p.read_text())['ok']\n"
    ),
    "tests/test_dir_glob.py": (
        "import pathlib\n"
        "def test_glob():\n"
        "    assert list(pathlib.Path('tests/probe_sets/').glob('*.jsonl'))\n"
    ),
    "tests/test_gone.py": "import importlib\n\ndef test_gone():\n    importlib.import_module('api.gone')\n",
    "tests/unit/__init__.py": "",
    "tests/unit/test_helpers_user.py": "from tests._helpers import HELP\n\ndef test_help():\n    assert HELP == 1\n",
    "tests/unit/test_relative.py": "from .. import _helpers\n\ndef test_rel():\n    assert _helpers.HELP == 1\n",
    "tests/fixtures/sample_payload.json": '{"ok": true}\n',
    "tests/probe_sets/set_a.jsonl": "{}\n",
    "docs/guide.md": "# guide\n",
    "canon/company.md": "# canon\n",
    "IFA/evidence/pack-2026-09-11.md": "evidence\n",
}


@pytest.fixture()
def repo(tmp_path: Path):
    for rel, text in FIXTURE_FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return sel.Repo(tmp_path)


def _tests(res) -> list[str]:
    return res["tests"]


# --- graph ------------------------------------------------------------------


def test_graph_resolves_absolute_and_relative_imports(repo):
    assert "rubric/scoring.py" in repo.imports["api/app.py"]
    assert "api/app.py" in repo.imports["tests/test_app.py"]
    assert "tests/_helpers.py" in repo.imports["tests/unit/test_helpers_user.py"]
    assert "tests/_helpers.py" in repo.imports["tests/unit/test_relative.py"]
    assert repo.test_files() == sorted(
        r for r in FIXTURE_FILES if r.startswith("tests/") and Path(r).name.startswith("test_")
    )


# --- fail-closed cases -------------------------------------------------------


@pytest.mark.parametrize(
    "changed",
    [
        ["tests/conftest.py"],
        ["locks/core-py312.lock"],
        ["requirements-dev.txt"],
        ["requirements/extra.txt"],
        ["scripts/ci_select_tests.py"],
        [".github/workflows/tests.yml"],
        ["tests/__init__.py"],
    ],
)
def test_invalidating_files_select_everything(repo, changed):
    res = sel.select(repo, changed)
    assert res["selection_mode"] == "ALL"
    assert _tests(res) == ["tests"]
    assert res["code"] is True
    assert all(res["gates"].values())


def test_empty_change_list_selects_everything(repo):
    res = sel.select(repo, [])
    assert res["selection_mode"] == "ALL"
    assert "fail closed" in res["reason"]


def test_full_flag_selects_everything(repo):
    res = sel.select(repo, ["docs/guide.md"], full=True)
    assert res["selection_mode"] == "ALL"


def test_core_module_without_importing_test_selects_everything(repo):
    res = sel.select(repo, ["api/orphan.py"])
    assert res["selection_mode"] == "ALL"
    assert "no importing test" in res["reason"]


def test_threshold_falls_back_to_all(tmp_path: Path):
    files = {"core/__init__.py": "", "core/x.py": "X = 1\n", "tests/__init__.py": ""}
    for i in range(10):
        files[f"tests/test_{i}.py"] = "from core.x import X\n\ndef test():\n    assert X\n"
    files["tests/test_other.py"] = "def test():\n    pass\n"
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    res = sel.select(sel.Repo(tmp_path), ["core/x.py"])
    assert res["selection_mode"] == "ALL"
    assert "cheaper" in res["reason"]


# --- narrow selection --------------------------------------------------------


def test_docs_only_change_runs_nothing(repo):
    res = sel.select(repo, ["docs/guide.md", "canon/company.md", "IFA/evidence/pack-2026-09-11.md"])
    assert res["selection_mode"] == "NONE"
    assert _tests(res) == []
    assert res["code"] is False
    assert not any(res["gates"].values())


def test_source_change_selects_only_importing_tests(repo):
    res = sel.select(repo, ["api/app.py"])
    assert res["selection_mode"] == "SOME"
    assert _tests(res) == ["tests/test_app.py"]
    gates = res["gates"]
    assert gates["aiuc1"] and gates["regression"] and gates["runtime_slice"] and gates["registry"]
    assert not gates["scoring"] and not gates["go"] and not gates["portal"]


def test_transitive_dependents_are_selected(repo):
    res = sel.select(repo, ["rubric/scoring.py"])
    assert res["selection_mode"] == "SOME"
    assert _tests(res) == ["tests/test_app.py", "tests/test_scoring.py"]
    assert res["gates"]["scoring"] is True


def test_changed_test_file_is_selected_itself(repo):
    res = sel.select(repo, ["tests/test_scoring.py"])
    assert _tests(res) == ["tests/test_scoring.py"]
    assert res["gates"]["scoring"] is True  # named in the scoring gate


def test_test_helper_change_selects_its_users(repo):
    res = sel.select(repo, ["tests/_helpers.py"])
    assert _tests(res) == ["tests/unit/test_helpers_user.py", "tests/unit/test_relative.py"]


def test_data_file_selects_tests_that_name_it(repo):
    res = sel.select(repo, ["rubric/probe_mappings/garak_beaaa152.yaml"])
    assert _tests(res) == ["tests/test_garak_registry_resolution_beaaa31623.py"]
    assert res["gates"]["evalvenv"] is True
    assert res["gates"]["scoring"] is True  # rubric/ prefix


def test_fixture_file_selects_by_basename(repo):
    res = sel.select(repo, ["tests/fixtures/sample_payload.json"])
    assert _tests(res) == ["tests/test_fixture_reader.py"]


def test_directory_glob_reference_counts(repo):
    res = sel.select(repo, ["tests/probe_sets/set_b.jsonl"])  # new file, only its dir is known
    assert _tests(res) == ["tests/test_dir_glob.py"]


def test_deleted_module_selects_tests_naming_it(repo):
    res = sel.select(repo, ["api/gone.py"])
    assert res["selection_mode"] == "SOME"
    assert _tests(res) == ["tests/test_gone.py"]


def test_untested_helper_script_runs_nothing(repo):
    res = sel.select(repo, ["scripts/helper.py"])
    assert res["selection_mode"] == "NONE"
    assert res["gates"]["runtime_slice"] is True  # scripts/ is in the runtime slice


def test_windows_paths_and_blanks_are_normalised(repo):
    res = sel.select(repo, ["api\\app.py", "", "  "])
    assert _tests(res) == ["tests/test_app.py"]


def test_gate_prefixes_alone_turn_jobs_on(repo):
    res = sel.select(repo, ["tools/are-collector/main.go"])
    assert res["selection_mode"] == "NONE"
    assert res["gates"]["go"] is True
    assert res["gates"]["aiuc1"] is True  # tools/ prefix, fail-closed
    assert res["gates"]["connector"] is False


# --- CLI surface -------------------------------------------------------------


def test_github_output_lines(repo, tmp_path: Path, monkeypatch):
    out = tmp_path / "out.txt"
    summary = tmp_path / "summary.md"
    monkeypatch.chdir(repo.root)
    rc = sel.main(
        [
            "--changed-json",
            json.dumps(["api/app.py"]),
            "--github-output",
            str(out),
            "--summary",
            str(summary),
        ]
    )
    assert rc == 0
    lines = out.read_text().splitlines()
    assert "selection_mode=SOME" in lines
    assert 'tests=["tests/test_app.py"]' in lines
    assert "code=true" in lines
    assert "aiuc1=true" in lines
    assert "scoring=false" in lines
    assert any(l.startswith("reason=") for l in lines)
    assert "### CI selection" in summary.read_text()


def test_malformed_json_fails_closed(repo, tmp_path: Path, monkeypatch, capsys):
    out = tmp_path / "out.txt"
    monkeypatch.chdir(repo.root)
    sel.main(["--changed-json", "not json", "--github-output", str(out)])
    assert "selection_mode=ALL" in out.read_text().splitlines()


@pytest.mark.parametrize(
    "mode, raw, expected",
    [
        ("ALL", '["tests"]', ["tests"]),
        ("", "", ["tests"]),  # changes job never reported -> whole tree
        ("NONE", "[]", []),
        ("SOME", '["tests/test_a.py", "tests/test_b.py"]', ["tests/test_a.py", "tests/test_b.py"]),
        ("SOME", "", ["tests"]),
        ("SOME", "garbage", ["tests"]),
        ("SOME", "[]", ["tests"]),
        # eval-venv-only tests are dropped from explicit targets: pytest honours
        # --ignore only for directory collection, so naming them would run them
        # in a job with no inspect_ai installed.
        (
            "SOME",
            '["tests/test_a.py", "tests/test_inspect_runner.py"]',
            ["tests/test_a.py"],
        ),
        ("SOME", '["tests/unit/test_inspect_provenance_pin.py"]', ["tests"]),
    ],
)
def test_expand_reads_env(monkeypatch, tmp_path, mode, raw, expected):
    # Preserve legacy expansion scenarios in an isolated legacy-only checkout.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SELECTION_MODE", mode)
    monkeypatch.setenv("SELECTED_TESTS", raw)
    assert sel._expand() == expected


def test_gate_table_names_only_real_tests():
    """Every test path named in GATES must exist, or the gate silently rots."""
    if not (ROOT / "tests" / "test_scoring.py").exists():
        pytest.skip("not running inside the are-scanner checkout")
    missing = [
        t
        for rule in sel.GATES.values()
        for t in rule["tests"]
        if not (ROOT / t).exists()
    ]
    assert missing == [], f"GATES names tests that do not exist: {missing}"


def test_eval_venv_only_tests_are_named_by_the_evalvenv_gate():
    """The drop-list must stay a subset of the gate that actually runs them."""
    assert sel.EVAL_VENV_ONLY_TESTS <= set(sel.GATES["evalvenv"]["tests"])


@pytest.mark.parametrize('invalidate', [False, True])
def test_changed_intake_privacy_file_is_selected_outside_root_tests(tmp_path, monkeypatch, invalidate):
    target = 'backbond/scan-intake/tests/test_report_release.py'
    path = tmp_path / target
    path.parent.mkdir(parents=True)
    path.write_text('def test_privacy(): pass\n')
    changed = [target] + (['scripts/ci_select_tests.py'] if invalidate else [])
    result = sel.select(sel.Repo(tmp_path), changed)
    assert any(target == item or target.startswith(item.rstrip('/') + '/')
               for item in result['tests'])
    assert result['code'] is True
    monkeypatch.setenv('SELECTION_MODE', result['selection_mode'])
    monkeypatch.setenv('SELECTED_TESTS', json.dumps(result['tests']))
    monkeypatch.chdir(tmp_path)
    assert any(target == item or target.startswith(item.rstrip('/') + '/')
               for item in sel._expand())


def _starter_intake_repo(tmp_path):
    files = {
        "backbond/scan-intake/server/app.py": "def status(): return {}\n",
        "backbond/scan-intake/web/scan.js": "// maintained page\n",
        "backbond/scan-intake/tests/test_report_release.py": "def test_public_projection(): pass\n",
        "backbond/scan-intake/tests/test_bac_intake.py": "def test_ack(): pass\n",
    }
    for index in range(10):
        files[f"tests/test_unrelated_{index}.py"] = "def test_other(): pass\n"
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return sel.Repo(tmp_path)


def test_starter_nested_test_change_is_never_selected_as_none(tmp_path):
    repo = _starter_intake_repo(tmp_path)
    path = "backbond/scan-intake/tests/test_report_release.py"
    assert path in repo.test_files()
    result = sel.select(repo, [path])
    assert result["selection_mode"] == "SOME" and result["code"] is True
    assert path in result["tests"]


@pytest.mark.parametrize("changed", [
    "backbond/scan-intake/server/app.py",
    "backbond/scan-intake/web/scan.js",
    "backbond/scan-intake/tests/test_report_release.py",
])
def test_starter_source_page_and_contract_changes_select_whole_intake_suite(tmp_path, changed):
    repo = _starter_intake_repo(tmp_path)
    result = sel.select(repo, [changed])
    assert result["selection_mode"] == "SOME"
    assert set(result["tests"]) == {
        "backbond/scan-intake/tests/test_report_release.py",
        "backbond/scan-intake/tests/test_bac_intake.py",
    }


def test_starter_full_and_fallback_expansion_include_intake_suite(tmp_path, monkeypatch):
    repo = _starter_intake_repo(tmp_path)
    expected = ["tests", "backbond/scan-intake/tests"]
    assert sel.select(repo, [], full=True)["tests"] == expected
    assert sel.select(repo, ["scripts/ci_select_tests.py"])["tests"] == expected
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SELECTION_MODE", "ALL")
    assert sel._expand() == expected
    monkeypatch.setenv("SELECTION_MODE", "SOME")
    monkeypatch.setenv("SELECTED_TESTS", "invalid json")
    assert sel._expand() == expected
