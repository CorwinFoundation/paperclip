import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".githooks" / "post-commit"
RELEASE_PUSH = REPO_ROOT / "scripts" / "push_release.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="Git hook/release guard tests exercise POSIX shell scripts.",
)


def run(cmd, cwd, **kwargs):
    env = os.environ.copy()
    env.pop("GITHUB_PAT_ARE_SCANNER", None)
    env.update(kwargs.pop("env", {}))
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, **kwargs)


def init_repo(tmp_path, branch="beaaa-5283-guard"):
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"

    run(["git", "init", "--bare", str(origin)], tmp_path, check=True)
    run(["git", "init", "-b", branch, str(repo)], tmp_path, check=True)
    run(["git", "config", "user.email", "scanner@example.invalid"], repo, check=True)
    run(["git", "config", "user.name", "Scanner Engineer"], repo, check=True)
    run(["git", "remote", "add", "origin", str(origin)], repo, check=True)

    (repo / "README.md").write_text("fixture\n")
    run(["git", "add", "README.md"], repo, check=True)
    run(["git", "commit", "-m", "initial"], repo, check=True)
    return repo, origin


def copy_hook(repo):
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    shutil.copy2(HOOK, hooks_dir / "post-commit")


def test_post_commit_auto_pushes_allowed_beaaa_branch(tmp_path):
    repo, origin = init_repo(tmp_path, branch="beaaa-5283-guard")
    copy_hook(repo)

    (repo / "change.txt").write_text("allowed\n")
    run(["git", "add", "change.txt"], repo, check=True)
    result = run(["git", "commit", "-m", "allowed branch"], repo, check=True)

    assert "[auto-push] pushed beaaa-5283-guard to origin" in result.stderr
    refs = run(["git", "show-ref"], origin, check=True).stdout
    assert "refs/heads/beaaa-5283-guard" in refs


def test_post_commit_skips_master(tmp_path):
    repo, origin = init_repo(tmp_path, branch="master")
    copy_hook(repo)

    (repo / "change.txt").write_text("master\n")
    run(["git", "add", "change.txt"], repo, check=True)
    result = run(["git", "commit", "-m", "master guard"], repo, check=True)

    assert "guarded branch 'master'" in result.stderr
    assert run(["git", "show-ref"], origin).stdout == ""


def test_post_commit_skips_outside_allow_list(tmp_path):
    repo, origin = init_repo(tmp_path, branch="scratch/local")
    copy_hook(repo)

    (repo / "change.txt").write_text("scratch\n")
    run(["git", "add", "change.txt"], repo, check=True)
    result = run(["git", "commit", "-m", "scratch guard"], repo, check=True)

    assert "outside the auto-push allow-list" in result.stderr
    assert run(["git", "show-ref"], origin).stdout == ""


def test_post_commit_skips_protected_marker(tmp_path):
    repo, origin = init_repo(tmp_path, branch="beaaa-5283-guard")
    run(["git", "config", "branch.beaaa-5283-guard.protected", "true"], repo, check=True)
    copy_hook(repo)

    (repo / "change.txt").write_text("protected\n")
    run(["git", "add", "change.txt"], repo, check=True)
    result = run(["git", "commit", "-m", "protected guard"], repo, check=True)

    assert "carries protected marker" in result.stderr
    assert run(["git", "show-ref"], origin).stdout == ""


def test_release_push_requires_ceo_or_cto_ack(tmp_path):
    repo, _origin = init_repo(tmp_path, branch="release/2026-06-14")

    result = run(["bash", str(RELEASE_PUSH)], repo)

    assert result.returncode == 2
    assert "BACKBOND_RELEASE_PUSH_APPROVER_ROLE=CEO or CTO" in result.stderr


def test_release_push_allows_cto_ack(tmp_path):
    repo, origin = init_repo(tmp_path, branch="release/2026-06-14")

    result = run(
        ["bash", str(RELEASE_PUSH)],
        repo,
        env={
            "BACKBOND_RELEASE_PUSH_APPROVER_ROLE": "CTO",
            "BACKBOND_RELEASE_PUSH_REF": "BEAAA-5283",
        },
        check=True,
    )

    assert "pushing 'release/2026-06-14' with CTO approval BEAAA-5283" in result.stderr
    refs = run(["git", "show-ref"], origin, check=True).stdout
    assert "refs/heads/release/2026-06-14" in refs
