from pathlib import Path
import subprocess

import pytest

from harness.pipeline import load_priors
from rubric.scoring import Rubric, score


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "prior_path", sorted((ROOT / "scripts" / "agent_priors").glob("*.json"))
)
def test_every_path_b_prior_exactly_covers_active_rubric(prior_path: Path) -> None:
    rubric = Rubric.load(ROOT / "rubric" / "rubric.yaml")
    _, scores = load_priors(prior_path, required_dim_ids=rubric.active_dim_ids)
    assert set(scores) == set(rubric.active_dim_ids)
    eval_outputs = {
        dim_id: {"raw": raw, "evidence_uri": f"stored://{prior_path.stem}/{dim_id}"}
        for dim_id, raw in scores.items()
    }
    result = score(
        rubric,
        eval_outputs,
        snapshot_hash="sha256:" + "0" * 64,
        forensic_bundle_uri=f"stored://{prior_path.stem}",
        corpus_version="path-b-prior-completeness@1",
        seed=42,
    )
    assert set(result["pillars"]) == set(rubric.pillars)


def test_batch_wrapper_records_real_exit_statuses(tmp_path: Path) -> None:
    wrapper = ROOT / "scripts" / "run_status_batch.sh"
    result = subprocess.run(
        [str(wrapper), str(tmp_path), "2", "sh", "-c", "exit 7"],
        check=False,
    )
    assert result.returncode == 1
    assert (tmp_path / "1.exit").read_text() == "7\n"
    assert (tmp_path / "2.exit").read_text() == "7\n"
