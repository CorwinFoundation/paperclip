# Local preflight rollout

Internal setup note, 2026-10-07. This stage follows the dedicated tests runner
rollout on srv1799443.hstgr.cloud. It moves the two jobs in
version-control-preflight.yml using a separate BACKBOND_LOCAL_PREFLIGHT switch.

The repository's September workflow usage metrics, inspected 2026-10-07, recorded
3,919 hosted minutes for preflight. Together with tests.yml's 27,969 minutes,
these workflows represented about 73% of September's 43,725.81 Linux minutes.
That is historical usage coverage, not a promised bill reduction: forks and
actors outside the trusted allowlist remain hosted, and future usage, storage
and subscription costs may differ.

Both jobs route to the dedicated backbond-ci runner only when the switch equals
true, the actor is CorwinFoundation or backbond-qa, and any PR head belongs to
this repository. Eligible jobs queue when the dedicated runner is busy or
offline; there is no automatic paid overflow.
The Python version, test commands, evidence generation, failure-only artifact
upload, check names, permissions and triggers remain unchanged.

After the first rollout is validated, set BACKBOND_LOCAL_PREFLIGHT=true and
dispatch version-control-preflight.yml on the migration branch. Verify both
canonical release identity and drift and required policy test before merging.
Then verify a same-repository PR event. Disabling or removing the switch returns
future jobs to hosted execution. An offline dedicated runner queues jobs;
cancel and restart queued runs after rollback.

Local validation: actionlint passed and the nine release-identity and
preflight-policy unit tests passed. Record the current branch and post-merge
runner results in the pull request before declaring this stage active.
