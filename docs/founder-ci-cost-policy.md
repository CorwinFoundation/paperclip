# Founder CI spending policy

Effective October 13, 2026. Authority: Ari Katz's instruction in Codex, recorded in [BEAAA-40719](https://paperclip-release.backbond.ai/BEAAA/issues/BEAAA-40719).

The founder selected queueing on the existing monthly-paid VM over purchasing additional GitHub runner minutes. With the local-CI opt-ins enabled, trusted same-repository work from `CorwinFoundation` and `backbond-qa` uses the isolated `backbond-ci` runner. Runner congestion, an offline runner, a suspected phantom job and delivery deadlines do not authorize automatic paid overflow.

PR #1135 restored hosted fallback and hosted preflight under a CEO/CTO unblock instruction. Ari explicitly rejected that spending decision and ordered its rollback. The unrelated grading acceptance in PR #1133 is preserved. An agent instruction to unblock a merge cannot override this spending policy.

Before changing paid routing, the proposer must obtain Ari's explicit approval and record its source on the company issue and PR, naming the exact proposed change, reason, expected additional cost and time limit. CEO, CTO and QA sign-off and green CI are insufficient. Existing hosted selectors, aggregate, native coverage, PR unit tests and image-build lanes remain the approved baseline; they are not newly authorized exceptions.

For runner trouble, inspect current job state, distinguish active work from a stalled listener, and recover it using existing permissions. If host access is unavailable, report the specific recovery action to Ari. Do not disable the local-CI opt-ins, weaken cost-policy tests or convert preflight to hosted capacity as a workaround.

The routing regression checks pin queue-only selection and dedicated preflight. Repository AGENTS.md and CLAUDE.md make this restriction discoverable by both agent runtimes. They provide a process and regression guard, not an identity boundary: company agents share the `CorwinFoundation` GitHub identity, so GitHub author/merger names alone cannot establish Ari's personal approval. Do not claim an approval was given merely because that account was used.
