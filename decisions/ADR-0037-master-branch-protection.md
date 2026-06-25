---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This ADR makes master the canonical, audit-safe branch for the BackBond Scanner by specifying the exact branch-protection settings to apply, while recording the two account-level blockers (PAT Administration scope + repo plan) that currently prevent enabling them."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-8219"]
proof_points_cited: ["GitHub run 28110788477 (tests job step breakdown)", "local pytest origin/master c96117b: 17 failed / 1802 passed", "GitHub API 403 on repos/CorwinFoundation/are-scanner/branches/master/protection", "GitHub API 403 rulesets: Upgrade to GitHub Pro or make this repository public"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Blocked on account owner (Founder): (1) grant Administration:write on GITHUB_PAT_ARE_SCANNER, and (2) enable private-repo branch protection by upgrading CorwinFoundation to GitHub Pro OR making the repo public (IP decision -> CEO/Founder). No rubric weight, scoring math, or tamper-resistance change in this ADR."]
---

# ADR-0037: master branch protection for CorwinFoundation/are-scanner

## Decision

Make `master` on `CorwinFoundation/are-scanner` the canonical, audit-safe
branch by applying GitHub branch protection. This ADR pins the **exact
settings to apply** so that enabling them is a mechanical step once the
account-level blockers below are cleared.

This is infrastructure / repo-governance. It changes no rubric point,
pillar weight, scoring math, judge model, or tamper-resistance posture,
so no Underwriter co-sign or Founder rubric gate is triggered. The only
escalation is account-owner action (see Blockers).

## Phase 1 settings (apply immediately once unblocked)

Classic branch-protection PUT
`repos/CorwinFoundation/are-scanner/branches/master/protection`:

```json
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
```

Mapping to the task requirements:

| Requirement | Setting |
| --- | --- |
| Protect from force pushes | `allow_force_pushes: false` |
| Protect from deletion | `allow_deletions: false` |
| Require PR before merging | `required_pull_request_reviews` present (0 approvals so a single-committer fleet is not locked out, but every change still goes through a traceable PR) |
| Require conversation resolution | `required_conversation_resolution: true` |
| Require linear history | `required_linear_history: true` (compatible: the fleet already lands work as squash/rebase, no merge commits) |
| No bypass except admin emergency override | `enforce_admins: false` — admins (the emergency lever) may merge an urgent fix, but only through a PR, preserving the traceable path. Non-admins cannot bypass. |
| Required status checks | `null` in Phase 1 — CI is currently red (see Diagnosis); requiring it now would lock out all merges. |

## Phase 2 settings — APPLIED 2026-06-24

Required status checks (live on `master`):

```json
"required_status_checks": {
  "strict": true,
  "contexts": ["golden scanner regression", "python pytest"]
}
```

**Important — contexts are GitHub Actions *job* names, not workflow
names.** A required status check matches the check-run's name, which for
Actions is the `jobs.<id>.name`. Using the workflow file name (e.g.
`scanner-regression` or `tests`) would never match a check and would
block every merge. The correct contexts are:

- `python pytest` — the **job** in the `tests` workflow
  (`.github/workflows/tests.yml`), our main Python test suite. Required
  only after a clean, stable passing run existed on `master` (two
  consecutive green runs, `8dff2576` and `7d7ceef0`, after the
  BEAAA-8295 fix; CI verified `1828 passed, 0 failed`).
- `golden scanner regression` — the **job** in the `scanner-regression`
  workflow, the scanner regression / golden N=5 gate. Green on every
  recent master commit.
- `strict: true` requires branches to be up to date before merge. Kept
  for audit-safety; revisit only if it creates excessive rebase friction
  for the fleet.

## Diagnosis of the currently-failing check (Phase 2 precondition)

The `tests` workflow ("python pytest") is **a real test-suite
regression, not a dependency or CI-config issue.** Evidence:

- In CI run `28110788477`, the steps *Install dependencies*, *Setup
  Python*, and *Prove pytest discovers integration regressions*
  (collection) all **succeed**; only the *Run tests* step fails. A
  dependency/CI-config problem would fail at install or collection.
- Reproduced locally against `origin/master` (`c96117b`):
  **17 failed, 1802 passed**. Representative failures:
  - `rubric.scoring.ScoringError: missing dim scores:
    ['ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL',
    'ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER']`
    (`test_invocation_context_e2e`)
  - `test_beaaa8125_tool_misuse_live_fire`,
    `test_beaaa8142_tool_misuse_real_agent_live_fire`
  - 11 failures in `test_golden_set_l2_gate.py`
  - `test_beaaa398_wiring`, `test_sla_beaaa1274`
    (`assert 'partial' == 'accepted'`)

`scanner-regression` (golden N=5 gate) passes on the same commits, so
the scanner scoring path is stable; the failures are in
tool-misuse live-fire wiring and the L2 gate tests. Fixing these is
tracked separately and is the gate for moving to Phase 2.

**RESOLVED 2026-06-24** (BEAAA-8258 → BEAAA-8295): the suite was repaired
and `master` CI went green (`1828 passed, 0 failed` on `7d7ceef0`, then
again on `8dff2576`). Phase 2 was applied on the strength of two
consecutive green runs.

## Blockers (account owner / Founder)

Both Phase 1 and Phase 2 are currently **un-appliable** through the
`GITHUB_PAT_ARE_SCANNER` credential:

1. **PAT scope.** GET and PUT on
   `branches/master/protection` both return
   `403 Resource not accessible by personal access token`. The
   fine-grained PAT can push commits (Contents:write) but lacks
   **Administration: write**, which branch protection requires.
2. **Repo plan.** `CorwinFoundation` is a **User** account and
   `are-scanner` is **private**. The rulesets API returns
   `Upgrade to GitHub Pro or make this repository public to enable this
   feature.` Branch protection / rulesets on a private repo require
   **GitHub Pro** (or making the repo public — an IP decision, since the
   scanner is BackBond's product).

### Unblock path

- Founder (GitHub account owner) grants **Administration: write** to
  `GITHUB_PAT_ARE_SCANNER` (or issues a token that has it), **and**
- Founder/CEO chooses: upgrade `CorwinFoundation` to **GitHub Pro**
  (~$4/mo) **or** make the repo public. Recommendation: **GitHub Pro**,
  to keep the scanner private per our IP/confidentiality principle.

Once both are cleared, applying Phase 1 is a single API call with the
payload above.

## Rollback

Branch protection is fully reversible: `DELETE
repos/CorwinFoundation/are-scanner/branches/master/protection` removes
all rules with no effect on history. No data migration.
