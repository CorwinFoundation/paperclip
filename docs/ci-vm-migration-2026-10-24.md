# Remaining CI migration assessment

Internal engineering note, October 24, 2026.

This change implements the request in the current chat to move remaining paid GitHub jobs to the existing dedicated VM wherever safe. It reduces hosted routing, adds no purchased capacity, changes neither local-CI opt-in, and permits no busy/offline overflow. The October 13 founder spending restriction remains unchanged.

## Selected jobs

For the existing trusted actors (`CorwinFoundation` and `backbond-qa`), with the existing opt-in enabled and same-repository PR heads:

| Workflow | Jobs selected for `backbond-ci` |
| --- | --- |
| tests | Affected-test selector and required pytest aggregate |
| PR Unit Tests | Selector, brain, portal, probe SDK, connector, collector Go tests, and production runtime import closure |
| log-listener | Python and Node test job |
| BEAAA-33964 ledger drift | Ledger reproduction check |
| Scanner Immutable GHCR Image | Read-only image-input classifier |
| weekly branch hygiene | Read-only branch report |

These 13 job definitions use the same explicit actor and repository boundary as the original migration. Their runner choice is evaluated directly rather than relying on a preceding selector succeeding. Eligible work queues when the VM is busy or offline. Forks and actors outside that boundary retain their existing hosted behavior; this change does not authorize them on persistent infrastructure.

Heavy jobs in `tests.yml` also require the selector job to succeed and emit a nonempty runner output before running. A selector infrastructure failure or missing runner output fails the required pytest aggregate instead of reaching the old hosted default. Malformed selector input still selects the full suite through the selector script's existing fallback, and normal affected-test selection is unchanged.

Newly migrated Python dependency jobs use a clean virtual environment under `RUNNER_TEMP`, which the runner clears between jobs. They do not install into shared Python or reuse a development environment for the production import-closure check. Local jobs do not create additional remote dependency caches. On October 26, persistent pip download caching was disabled to preserve the disposable build pool's disk reservation; installed test/eval virtual environments remain cached. The log-listener job provisions Node 22. The collector uses setup-go with its existing Go 1.22 selection. The branch report installs the official GitHub CLI in the job's temporary directory with a pinned archive checksum, without sudo.

The image classifier explicitly receives read-only permissions instead of inheriting image-publication and OIDC write permissions. No publishing, signing or deployment credentials are added to the general-purpose test runner.

The portal's Poppler dependency is included in the runner Containerfile and was provisioned inside the running rootless container through the browser terminal on October 24. The workflow requires `pdftotext` to be present and fails if it is missing. It does not attempt sudo on the dedicated runner.

After provisioning, `dpkg --audit` reported no incomplete packages and `pdftotext -v` reported version 24.02.0 when executed as the normal `backbond-ci` user. The runner still had UID 1001, zero effective capabilities, `NoNewPrivs=1`, no Docker socket or readable root SSH key, the existing 5 GiB memory limit, and only its existing named home volume mounted. No runner restart was needed.

The log-listener suite contains a browser regression that would otherwise skip without global Google Chrome. Its dedicated job installs the Chromium version matched to the existing pinned Playwright npm package, sets an explicit executable path only for that job, and verifies in its JUnit report that the browser case executed without skipping. The same browser assertions are preserved. Unrelated jobs keep their existing browser-discovery behavior. Required native libraries were provisioned inside the runner and are included in its Containerfile, following the [Playwright 1.61.1 Ubuntu 24.04 dependency list](https://github.com/microsoft/playwright/blob/v1.61.1/packages/playwright-core/src/server/registry/nativeDeps.ts).

## Compatibility and isolation exceptions

| Jobs | Reason |
| --- | --- |
| Docker/nginx integrations, Kaltura browser-image check, CI-plugin container build | The isolated runner has no Docker daemon/socket or sudo. Relabeling these jobs would fail coverage. Mounting the host socket or granting privileged container access would weaken the current boundary. |
| Scanner image publication, collector release, QA/release evidence, image verification and legacy signing workflows | Require Docker; publishing/signing lanes also receive write permissions or OIDC. They need a separately isolated build environment before migration. |
| Agent Scan Friday scoreboard writer | Uses a repository write token. Keep it separate from the persistent PR test environment. |
| QA Authority shadow | Uses `pull_request_target`, checks write and OIDC. Preserve the guard that excludes privileged PR-target jobs from the shared test runner. Currently disabled by its existing opt-in. |
| Darwin build | Native macOS/architecture verification cannot run on this Linux x64 VM. Manual-only. |

No check or test command is removed to reduce spend. Required branch-protection context names remain unchanged. Storage charges and retained hosted lanes remain billable; this migration alone cannot establish zero Actions charges.

## Validation

Run the cost-policy and PR-runner isolation tests, selector/sharding tests, and actionlint on changed workflows. The manual Dedicated CI runner smoke workflow additionally exercises the disposable Python action, routing guards, checksum-verified GitHub CLI, and collector Go tests on `backbond-ci`.

Validate the changed test jobs on the dedicated runner before activating this migration. Windows-only local checks do not establish Linux workload compatibility. Record exact GitHub run results in the PR. If an eligible job fails or queues, repair or recover the existing runner; do not return migrated jobs to hosted execution as an automatic workaround.
