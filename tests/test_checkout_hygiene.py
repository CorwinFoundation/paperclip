from pathlib import Path
import importlib.util
import sys

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "checkout_hygiene.py"
SPEC = importlib.util.spec_from_file_location("checkout_hygiene", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def checkout(path, *, ticket=None, clean=True, merged=True, current=False):
    return MODULE.Checkout(path, "a" * 40, "branch", ticket, clean, merged, current)


def test_ticket_mapping_accepts_branch_or_path():
    assert MODULE.ticket_for("C:/scanner", "agent/beaaa-15613-hygiene") == "BEAAA-15613"
    assert MODULE.ticket_for("C:/worktrees/BEAAA_99", "detached") == "BEAAA-99"


def test_selection_reaches_five_and_prefers_unowned_merged_clean():
    rows = [checkout("root", current=True)] + [checkout(f"owned-{n}", ticket=f"BEAAA-{n}") for n in range(1, 5)]
    rows += [checkout("unowned-b"), checkout("unowned-a")]
    assert [c.path for c in MODULE.select_retirements(rows, 5)] == ["unowned-a", "unowned-b"]


def test_selection_refuses_dirty_or_unmerged_excess():
    rows = [checkout("root", current=True)] + [checkout(str(n), ticket=f"BEAAA-{n}", clean=False) for n in range(5)]
    try:
        MODULE.select_retirements(rows, 5)
    except RuntimeError as error:
        assert "cannot reach maximum 5" in str(error)
    else:
        raise AssertionError("unsafe excess should fail closed")
