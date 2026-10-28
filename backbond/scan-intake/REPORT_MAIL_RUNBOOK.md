# BEAAA-43820 source and held operations

Internal handoff for CTO and the retained Scanner Engineer #3 owner. PR #1239
extends the intake service on port 8793. No install, identity change, merge,
deployment, service restart, provider call, test email or report delivery is
authorized by this runbook. Ari's 23 October recovery instruction retains those
holds. No schedule, heartbeat, worker or CI routing change is needed.

## Contract and limits

`POST /scan/v1/reports/mail` accepts the existing client's JSON
`schema_version=report_mail@1`, both base64 artifacts and SHA-256 hashes, target,
ISO run date, note, mode, recipients and approval reference. The endpoint checks
the exact configured BAC tenant/key, active status, finite future expiry and
explicit `POST /scan/v1/reports/mail` scope before reading the body. Flat-token
fallback and legacy unscoped keys cannot authorize this capability.

Body limit: 35 MiB, streamed before JSON parsing. PDF: 20 MiB, `%PDF-` prefix;
HTML: 5 MiB, UTF-8. Target: 200 characters, note: 2,000; maximum combined
recipient inputs: 50. Founders come only from `QA_REPORT_MAIL_FOUNDERS`.
Customer mode requires explicit recipients and a `BAC-<positive number>` approval
card reference and copies the configured founders. The card remains the human
approval record; this endpoint checks the reference, not card contents.

The original intake sender and `scan-mail.json` credential feed the shared Resend
transport. PDF attachment bytes are unchanged. The durable SQLite binding freezes
actor, recipients, approval reference, artifacts/hashes, note and report locator.
An identical payload and key identity reuse one claim despite configuration drift.
The server derives its own claim identity; it does not trust the caller's
Idempotency-Key header. Provider acceptance is recorded as `accepted`, never as
inbox delivery. Unknown sends retry only with the same immutable body/key; a
120-second lease prevents concurrent sends, and the claim holds for reconciliation
after 23 hours from its first attempt. Reinvoke the identical client command for
an operator-authorized retry. There is no background sender or timer.

Mailed links locate `/scan/v1/mailed-reports/<claim>` and contain no session secret.
Each intended recipient verifies their own inbox through the intake's existing
`issue_report_code`, `verify_report_code` and `authorize_report` implementation.
The adapter changes only recipient lookup; it creates no synthetic scan or legacy
release. Existing limits remain: five issues per client/hour, fifty per recipient
report/hour, five failed guesses/challenge, ten-minute challenges and session idle
expiry, thirty-minute session maximum. Cookies are Secure, HttpOnly, SameSite
Strict and report-path scoped. Unknown recipients get the same response without
mail. Supplied HTML is returned only after verification, as an attachment with
sandbox CSP, no script execution on the API origin and no-store headers.

## Packaging and exact commands (prepared only)

Scanner modules are included by both existing `install-api.sh` and
`update-server.sh`, including the maintained `api/edge_gateway.py` resolver.
The existing sealed mail credential stays in its current systemd LoadCredential.
`QA_REPORT_MAIL_ENABLED=0` is the default. No existing BAC/Operations queues change.
The service needs read access to the existing issuer-maintained `ARE_KEY_STORE_PATH`
and its explicit BAC tenant/key identifiers. No key-store discovery or credential
copy was performed here.

For srv2066639, the client-only installer and the adjacent systemd drop-in example
live beside the existing BAC service-unit packaging. Once released, Platform
selects the **existing** BAC Python interpreter with httpx and the **existing**
service unit/credential placement. This installs no Python dependencies and
starts no service:

```sh
sudo sh backbond/scan-intake/install-report-mail-client.sh /ABSOLUTE/EXISTING/BAC/PYTHON
```

`systemd/report-mail-client.conf.example` shows the LoadCredential mapping. It
requires substitution of the existing sealed service credential path; it must
not be installed literally. Unit identity and credential placement remain the
operator's existing handoff. No founder credential is required.

The BAC CEO invocation, once installation, explicit mail scope and delivery are
separately released, is:

```sh
backbond-report-mail --base-url https://api.backbond.ai \
  --credential-file "$CREDENTIALS_DIRECTORY/report-mail-bac" \
  --mode founders --pdf /VERIFIED/BAC-74-round-4.pdf \
  --html /VERIFIED/BAC-74-round-4.html --target Resend \
  --run-date 2026-10-23 --note 'test of report mail'
```

Those artifact paths are required operator inputs, not verified local files.
Before the held one-mail test, the owner must verify BAC-74 attachment identities
7fe9766a / 9dac3eb2 and their recorded hashes, and verify founders configuration
is ari@backbond.ai,ericg@backbond.ai. No attachment fetch or test send occurred.
Customer invocation adds `--mode customer --recipient ADDRESS --approval-ref BAC-N`
in place of founders mode and requires a separately approved delivery.
Exit codes: 0 provider accepted; 2 valid receipt still pending/unknown/held;
1 request/input failure. Only safe request/state/provider-id receipt fields print.

The missing permission is an explicit mail action on the maintained finite-lived
BAC key. Platform/identity custodian owns that held grant after Ari releases it;
it is not a reason to stop source work. This package neither grants a scope nor
prints bearer material. An absent grant keeps submission denied.

## Verification and rollback

Focused command (mock provider only):

```sh
python3 -m pytest tests/test_beaaa39328_report_access.py backbond/scan-intake/tests/test_report_mail*.py -q
```

69 checks passed at this source checkpoint. Shell syntax and `git diff --check`
passed. Existing required PR CI selects the full intake closure and queues heavy
work on `backbond-ci`; preflight uses its existing isolated route. No workflow,
runner label, capacity, opt-in or paid fallback has been changed. Required CI
must pass on the pushed revision before source completion is declared.

After a separately authorized deployment, operational rollback first closes
`QA_REPORT_MAIL_ENABLED`, then uses the existing deploy helper rollback. Preserve
SQLite claims, verification/session state and the first-send timestamps; restoring
stale pre-send state could duplicate delivery. Revert source or fix forward through
the existing PR policy. Keep the original mail credential, legacy verifier,
recipient data and normal worker queues intact. No rollback command was executed.


## Combined native Findings source reconciliation (23 October)

The refreshed GitHub master is `94a0c9a0abbf686126935adde6a3aa8380babaaf`
(PR #1237), descended from `73d4e060`. PR #1239 merges this master without
replacing any #1237 Findings client, events, gateway or workflow source.
The app retains #1237's notification wakeup and worker lifecycle alongside the
bounded report-mail router. Report mail remains disabled by default and requires
its own exact finite-lived BAC principal/scope; native workflow authentication
and SHIP author/evidence checks are unchanged.

These are separate triggers with separate immutable claims. Native Findings
sends its independently reviewed exact PDF/HTML attachments only to the original
scan recipient, and records a native delivery receipt. The Customer Operations
submission sends the supplied PDF plus recipient-verified HTML/PDF access through
the shared intake transport. It does not create, approve, acknowledge, or expedite
a native Findings workflow. Neither outbox deduplicates the other. Operators must
not submit the same report through both paths. The native Findings sender's
existing provider body, key and retry policy are preserved; source reconciliation
does not authorize changing those approved delivery semantics.

`update-server.sh` now includes the transitive native Findings Python modules
(`bac_intake`, `findings_intake`, `findings_client`, `findings_workflow`,
`findings_events`) alongside the report-mail modules and edge identity resolver.
`install-api.sh` already includes all server Python files. The report-mail client
installer uses an existing httpx-capable BAC interpreter and installs no packages.
The native gateway additionally requires the unchanged exact dependency pin
`websockets==15.0.1` in `server/requirements-findings-events.txt`. Platform owns
confirming that pin in the existing gateway environment before any separately
authorized activation. No experimental environment install or live package
inspection is evidence of acceptance here. Artifact/report source hashes,
selection/corpus pins, native review gates and the authorized #1237 source remain
unchanged. Runtime source/package identity and the mail scope grant remain held
operational prerequisites, not completed deployment claims.
