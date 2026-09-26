"""Fast-forwarding the scan checkout must also make /etc match it.

BEAAA-35698: the nightly model-zoo scan was dead from 2026-09-12 to 2026-09-25
on ``PermissionError: [Errno 13] Permission denied:
/var/lib/backbond/scans/.worker-home`` -- a filesystem permission, not
OpenRouter credit.  It is the same failure BEAAA-30934 diagnosed on the weekly
Vapi lane: /etc/systemd/system held a pre-#558 unit, so ``HOME`` was unset and
``_ensure_worker_home()`` died EACCES on the 0750 parent.  The repair landed in
the repo as ``0dca4c9e4`` and never reached the box.

BEAAA-30934 built the detector (``check-unit-drift.sh``) and the repair
(``install-units-only.sh``) and deliberately wired neither into any unit --
``tests/test_scheduled_scan_beaaa30240.py`` pins that the scan units invoke no
helper of this kind.  So the drift went unnoticed for thirteen days because
nothing ran the detector.  ``backbond-scan-schedule-update`` is where it now
runs: an operator-invoked script, on the other side of the merge that creates
the drift.

These tests own that script's logic.  The real detector runs for real against a
real temp unit dir; the repair step is shimmed in the repair case so the test
needs no privileges, because ``install-units-only.sh`` is already covered end to
end by tests/test_beaaa30934_install_units_only.py.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE_DIR = ROOT / "ops" / "scan-schedule"
SCRIPT_NAME = "backbond-scan-schedule-update"
UNITS = sorted(
    p.name for p in SCHEDULE_DIR.iterdir() if p.suffix in {".service", ".timer"}
)


@pytest.fixture()
def host(tmp_path: Path):
    """A fake checkout plus unit dir, and a callable that runs the script.

    ``git`` is shimmed: the fetch/merge half is not what these tests own, and a
    real remote would make them network-dependent.
    """

    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    (shim_dir / "git").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (shim_dir / "git").chmod(0o755)

    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    shutil.copytree(SCHEDULE_DIR, checkout / "ops" / "scan-schedule")
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()

    def run(*, as_root: bool = False) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{shim_dir}:{env['PATH']}"
        if as_root:
            (shim_dir / "id").write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
            (shim_dir / "id").chmod(0o755)
        return subprocess.run(
            ["sh", str(SCHEDULE_DIR / SCRIPT_NAME), str(checkout), str(unit_dir)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    run.checkout = checkout  # type: ignore[attr-defined]
    run.unit_dir = unit_dir  # type: ignore[attr-defined]
    run.shim_dir = shim_dir  # type: ignore[attr-defined]
    return run


def _install_units(unit_dir: Path) -> None:
    for name in UNITS:
        shutil.copyfile(SCHEDULE_DIR / name, unit_dir / name)


def test_matching_units_are_reported_and_nothing_is_repaired(host):
    _install_units(host.unit_dir)
    proc = host()
    assert proc.returncode == 0, proc.stderr
    assert "nothing to repair" in proc.stdout


def test_absent_units_are_reported_as_drift(host):
    proc = host()
    assert proc.returncode == 1
    assert "drifted" in proc.stderr
    assert "install-units-only.sh" in proc.stderr


def test_a_stale_installed_unit_is_detected(host):
    """The BEAAA-35698 failure itself: /etc held a unit older than the repo."""

    _install_units(host.unit_dir)
    stale = host.unit_dir / "backbond-scan-zoo.service"
    stale.write_text(
        (SCHEDULE_DIR / "backbond-scan-zoo.service")
        .read_text(encoding="utf-8")
        .replace("Environment=HOME=/var/lib/backbond/scan-home\n", ""),
        encoding="utf-8",
    )
    proc = host()
    assert proc.returncode == 1
    assert "DRIFT" in proc.stdout
    assert "backbond-scan-zoo.service" in proc.stdout


def test_root_repairs_the_drift_and_proves_it(host):
    """As root the script repairs, then re-checks, and reports what it left."""

    installer = host.checkout / "ops" / "scan-schedule" / "install-units-only.sh"
    log = host.shim_dir / "installer.log"
    installer.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'for u in {" ".join(UNITS)}; do cp "{SCHEDULE_DIR}/$u" "$1/$u"; done\n',
        encoding="utf-8",
    )
    installer.chmod(0o755)

    proc = host(as_root=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert log.read_text(encoding="utf-8").strip() == str(host.unit_dir)
    assert "Units repaired" in proc.stdout
    # The re-check is the proof: every unit reports MATCH after the repair.
    assert proc.stdout.count("MATCH") >= len(UNITS)


def test_unprivileged_reports_drift_on_a_checkout_it_cannot_refresh(host):
    """The real box: /opt/backbond/src is not writable by the operator.

    `git fetch` there dies EACCES on `.git/FETCH_HEAD`, and because the fetch ran
    before the drift check under `set -e`, the unprivileged run aborted with a git
    error and never reported the drift it exists to report. The other tests shim
    `git` to exit 0, so none of them could see it. This one shims `git` to fail
    the way the box fails.
    """

    (host.shim_dir / "git").write_text(
        "#!/bin/sh\necho \"error: cannot open '.git/FETCH_HEAD': Permission denied\" >&2\nexit 128\n",
        encoding="utf-8",
    )
    (host.shim_dir / "git").chmod(0o755)

    proc = host()
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "drifted" in proc.stderr
    assert "install-units-only.sh" in proc.stderr
    assert "not refreshing it" in proc.stderr
    assert "FETCH_HEAD" not in proc.stderr


def test_root_still_fast_forwards_before_checking(host):
    """The skip is scoped to the unprivileged path; root must still merge first.

    A repair that installs units from a checkout nobody refreshed would put the
    stale copy back under /etc, which is the whole failure this script exists to
    end.
    """

    log = host.checkout.parent / "git-calls"
    (host.shim_dir / "git").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> {log}\nexit 0\n', encoding="utf-8"
    )
    (host.shim_dir / "git").chmod(0o755)
    # Units already match, so the run stops at "nothing to repair" and needs no
    # chown it cannot perform; the point here is only what git was asked to do.
    _install_units(host.unit_dir)

    proc = host(as_root=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nothing to repair" in proc.stdout
    calls = log.read_text(encoding="utf-8")
    assert "fetch --quiet origin master" in calls
    assert "merge --ff-only --quiet origin/master" in calls


def _code() -> str:
    """The script with comments stripped, so pins are about what it runs."""

    text = (SCHEDULE_DIR / SCRIPT_NAME).read_text(encoding="utf-8")
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_no_service_is_ever_started_and_never_with_now():
    """BEAAA-30240: both timers carry Persistent=true.

    `--now` is banned rather than merely avoided. Arming the zoo timer means
    starting it, and `enable --now` would start it before the stamp seed below
    exists -- which is exactly the immediate catch-up run the seed prevents.
    Starting a *service* directly is never this script's business.
    """

    code = _code()
    assert "--now" not in code
    assert "systemctl start backbond-scan-vapi" not in code
    for line in code.splitlines():
        if "systemctl start" in line:
            assert line.strip().endswith(".timer"), line


def test_the_zoo_timer_is_armed_now_and_not_at_the_next_boot():
    """CEO measurement, BEAAA-35698 2026-09-26: the zoo timer has never fired.

    `UnitFileState=disabled`, `ActiveState=inactive`, `LastTriggerUSec` empty,
    no symlink in timers.target.wants. `enable` alone writes the symlink and
    leaves the unit inactive until the next boot, so the corrected units would
    fix the permission bug and still leave the lane dark. It must also start.
    """

    code = _code()
    assert "systemctl enable backbond-scan-zoo.timer" in code
    assert "systemctl start backbond-scan-zoo.timer" in code


def test_the_stamp_is_seeded_before_the_zoo_timer_starts():
    """Order is the whole safety property.

    With no stamp file a Persistent timer treats the last elapse as missed and
    triggers the moment it starts. Seeding first moves the first trigger to the
    next 03:30 UTC; seeding after, or not at all, fires an unfunded zoo run at
    whatever moment the operator runs this script.
    """

    code = _code()
    assert code.index("_seed_timer_stamp backbond-scan-zoo.timer") < code.index(
        "systemctl start backbond-scan-zoo.timer"
    )
    # An existing stamp carries real history and must not be overwritten.
    body = code[code.index("_seed_timer_stamp() {"):]
    assert 'test ! -e "$stamp" || return 0' in body


def test_the_vapi_timer_is_disarmed_and_never_enabled():
    """CEO constraint, BEAAA-35698 2026-09-25.

    `/etc/backbond/scan-schedule.conf` points BACKBOND_SCAN_AGENT_ENDPOINT at
    https://api.vapi.ai -- live production -- and BEAAA-32522 forbids new VAPI
    traffic without a current written authorization, which lapsed in July. The
    timer is enabled, active and Persistent=true, and the only reason its
    2026-09-14 and 2026-09-21 firings sent nothing is the drifted unit this
    script repairs. Repairing without disarming would re-arm that lane.
    """

    code = _code()
    vapi_lines = [ln.strip() for ln in code.splitlines() if "vapi" in ln and "systemctl" in ln]
    assert vapi_lines, "the VAPI timer must be handled explicitly, not by omission"
    for line in vapi_lines:
        assert "systemctl enable" not in line, line
    joined = "\n".join(vapi_lines)
    assert "systemctl stop backbond-scan-vapi.timer" in joined
    assert "systemctl disable backbond-scan-vapi.timer" in joined


def test_only_the_zoo_timer_is_enabled_and_only_by_literal_name():
    """No glob, no loop: a future unit must not be armed by being added.

    The CEO constraint is that the provisioning path enables exactly
    `backbond-scan-zoo.timer`. A `for unit in ...timer` loop or a
    `backbond-scan-*.timer` glob would satisfy today's unit list and arm
    tomorrow's.
    """

    code = _code()
    enable_lines = [
        ln.strip() for ln in code.splitlines() if "systemctl enable" in ln
    ]
    assert enable_lines == ["systemctl enable backbond-scan-zoo.timer"]
    assert "backbond-scan-*.timer" not in code
    assert "*.timer" not in code


def test_disarming_happens_before_the_repair():
    """An aborted repair must not leave the VAPI lane armed and working."""

    code = _code()
    assert code.index("_scope_timers\n") < code.index("install-units-only.sh")
    # And the result is re-read afterwards rather than assumed.
    assert code.index("install-units-only.sh") < code.rindex("_assert_vapi_disarmed")


def test_timer_scoping_is_gated_on_the_real_unit_directory(host):
    """Enablement is global state, so a temp-dir run must not touch systemd."""

    shim = host.shim_dir / "systemctl"
    log = host.shim_dir / "systemctl.log"
    shim.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\n', encoding="utf-8")
    shim.chmod(0o755)

    _install_units(host.unit_dir)
    proc = host(as_root=True)
    assert proc.returncode == 0, proc.stderr
    assert not log.exists(), log.read_text(encoding="utf-8") if log.exists() else ""


def test_the_script_uses_the_checkout_it_just_fast_forwarded():
    """Repairing from the repo's own copy would defeat the point.

    The units that must reach /etc are the ones the merge just brought in, so
    both helpers are invoked out of ``$checkout``, not out of wherever this
    script happens to sit.
    """

    code = (SCHEDULE_DIR / SCRIPT_NAME).read_text(encoding="utf-8")
    assert 'schedule_dir="$checkout/ops/scan-schedule"' in code
    assert '"$schedule_dir/check-unit-drift.sh"' in code
    assert '"$schedule_dir/install-units-only.sh"' in code
