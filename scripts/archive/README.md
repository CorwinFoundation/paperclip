# `scripts/archive/` — archived one-off scripts

**BLUF.** This directory holds per-ticket, single-use scripts that have been superseded and are no
longer referenced anywhere in the tracked tree (no CI workflow, no release gate, no test, no
reproducibility runbook, no other script). They are **archived, not deleted** — full git history is
preserved via `git mv`, so any prior calibration/live-fire run can still be reproduced from history.

Archived under BEAAA-12025 (periodic `scripts/`+`tools/` audit; parent BEAAA-12018 item #4, LOW/backlog).

## Selection rule (mechanical, zero-breakage)

A script was archived here **iff both**:
1. its name matches the per-ticket one-off pattern `beaaa<digits>_*.py` (ticket-scoped by construction), **and**
2. it has **zero references** anywhere in the tracked tree outside `artifacts/` and its own file
   (verified with `git grep -Il --fixed-strings <name> -- ':!artifacts/' ':!scripts/<name>'`
   returning empty at `origin/master`).

Nothing referenced by CI (`.github/workflows/`), a release gate (`release_gate_*`, `check_*`),
a verifier (`verify_*`), a test, a runbook, an ADR/cosign packet, the `harness/`, or another live
script was touched. General-purpose utilities and reproducibility fetchers (`fetch_bbq.py`,
`fetch_donotanswer.py`, `path_a_baselines.py`, `oss_evidence_pack_v1.py`, `measured_leaderboard.py`,
`score_real_agents.py`, etc.) were **kept in `scripts/`** even where currently unreferenced, pending
a future pass or CTO judgment.

## Calibration impact

No behavior change to any CI / release / scoring path. Golden set is unaffected (goldens live under
`tests/` and invoke none of these files). N=5 calibration delta on the golden set = **Δ0 by
construction** — a pure `git mv` of unreferenced files cannot alter scoring.

> **Reference check note.** Selection uses *module-name* matching (`beaaa758_l2_bias_pack_run`), not
> just filename (`...py`) — several one-off scripts are imported by a sibling `tests/test_*.py`
> without the `.py` suffix and are therefore load-bearing for the test suite. Those (e.g. beaaa758,
> beaaa8125, beaaa8142, beaaa8225, beaaa8389) were **kept in `scripts/`**.

## Archived contents (21 scripts)

| Script | Ticket | What it was |
| --- | --- | --- |
| beaaa1277_syllable_apichat_smoke.py | BEAAA-1277 | Syllable apichat smoke run |
| beaaa152_pack_runner.py | BEAAA-152 | Lightweight pack runner (Promptfoo-CLI alternative) |
| beaaa152_phase1_rehearsal.py | BEAAA-152 | Phase-1 rehearsal harness |
| beaaa198_phase3_emission_proof.py | BEAAA-198 | Phase-3 emission proof |
| beaaa206_calibration_delta_n5.py | BEAAA-206 | Per-ticket N=5 calibration delta |
| beaaa209_dryrun_p1_6.py | BEAAA-209 | P1.6 dry-run |
| beaaa2175_pra_v2_calibration.py | BEAAA-2175 | PRA v2 calibration |
| beaaa3009_pii_backfill.py | BEAAA-3009 | PII backfill one-off |
| beaaa3054_calibration_sweep.py | BEAAA-3054 | Calibration sweep |
| beaaa3054_rollback_test.py | BEAAA-3054 | Rollback test |
| beaaa334_n5_proxy_on.py | BEAAA-334 | N=5 proxy-on run |
| beaaa378_status_template.py | BEAAA-378 | Status template generator |
| beaaa387_persist_deterministic_baselines.py | BEAAA-387 | Persist v0.2.3 baseline artifacts |
| beaaa419_persist_huw_anchored.py | BEAAA-419 | Persist HUW-anchored baselines |
| beaaa484_calibration_delta_n5.py | BEAAA-484 | Per-ticket N=5 calibration delta |
| beaaa539_n5_calibration.py | BEAAA-539 | Per-ticket N=5 calibration |
| beaaa545_caps_n5.py | BEAAA-545 | Caps N=5 run |
| beaaa553_local_echo_smoke.py | BEAAA-553 | Local-echo ScanRuntime smoke |
| beaaa553_tclose_n5.py | BEAAA-553 | T-close N=5 run |
| beaaa675_render_loi_screenshots.py | BEAAA-675 | LOI screenshot renderer |
| beaaa749_n5_calibration.py | BEAAA-749 | Per-ticket N=5 calibration |

## Restoring a script

`git mv scripts/archive/<name> scripts/<name>` (or `git log --follow scripts/archive/<name>` to read
history). Nothing else references these, so restoring one is inert until you wire it back up.
