# Distribution Readiness Checklist — ARE Probe SDK R1 → R2

**Status: NOT ACTIONED.** Under Founder comms-freeze. R1 build is on the
shelf; this list defines what a new R2 ticket must add when the Founder
lifts the freeze and authorizes design-partner distribution.

## R2 prerequisites (each gets its own BEAAA ticket)

### Distribution surface

- [ ] Decide repo location: private GitHub under IFA org with DP-allowlist read,
      or PyPI publish, or tarball-via-portal. Default: tarball-via-portal.
- [ ] Publish path approved by CMO (positioning) and CTO (technical posture).
- [ ] Per-DP install path documented; assumes no DP touches IFA internal
      corpus directly (DP gets the SHA-pinned subset under a redistributable
      license-equivalent posture).

### Corpus

- [ ] Carve a redistributable corpus subset from the internal pack.
- [ ] Strip / re-license any corpus probe whose upstream license does not
      permit redistribution (DoNotAnswer / BBQ / AdvBench — each upstream
      license re-checked).
- [ ] Sign the redistributable corpus pack with `cto-attest-v1`; SDK loader
      verifies the signature before use.
- [ ] Publish corpus pack SHA in the R2 contract version bump.

### Multi-model client

- [ ] OpenAI HTTP client adapter (chat-completions + responses).
- [ ] Anthropic HTTP client adapter (messages).
- [ ] OpenRouter passthrough adapter.
- [ ] Per-adapter retry / timeout / rate-limit posture pinned in lockfile.
- [ ] Adapter unit tests against deterministic fixture servers (no live
      provider calls in CI).

### DP-signed envelopes

- [ ] DP key provisioning flow: DP-side keygen + public-key registration
      with IFA portal so IFA verifies inbound envelopes against the DP's
      registered key.
- [ ] Signature chain: DP-signed first, then CTO counter-sign on portal
      ingest. Schema bump in `are-scanner-api` for `signature_chain` (likely
      v1.1 additive).
- [ ] Key rotation procedure documented; DP-side rotation does not invalidate
      bound policies (per `are-scanner-api-v1.md` deprecation policy).

### DP-facing docs

- [ ] Onboarding guide (`docs/onboarding.md`) — install, config, smoke test,
      first envelope, hand-back channel.
- [ ] Operations runbook (`docs/operations.md`) — re-scan cadence, score
      drift triage, evidence bundle hand-back.
- [ ] Privacy posture statement (`docs/privacy.md`) — what stays local, what
      gets signed, what the DP ships to IFA.
- [ ] Each doc passes the canon banned-term grep (no ARI / BMI / BEAI / IFA-hosted).

### Distribution-side telemetry posture

- [ ] R1 has NO telephone-home. R2 must decide: opt-in error reporting?
      none? portal-only failure beacon? Default: none. If opt-in, must be
      Founder-approved and disabled by default.
- [ ] If any opt-in telemetry is added, document exactly what is collected
      and provide a strict-local-only flag.

### Security review

- [ ] Adversarial-input review of the DP-side CLI (every input is treated
      as attacker-controlled, mirroring `bin/are-scan`'s posture).
- [ ] Auth-mode review: bearer-token storage, env var vs config file, never
      log.
- [ ] Dependency security scan; lockfile audit.

### Legal review

- [ ] DP user license terms approved by Legal.
- [ ] Per-corpus upstream license redistribution clearance (BBQ / DoNotAnswer
      / AdvBench).
- [ ] DPA addendum for DP-side data handling if any envelope embeds DP data.

### CMO sign-off

- [ ] Positioning copy in README + onboarding guide passes CMO FAL audit.
- [ ] Carrier Rule compliance: no customer naming, no carrier naming, no
      premium quotes in any shipped artifact.

### CTO sign-off

- [ ] R2 contract version bump approved against `are-scanner-api-v1.md`.
- [ ] N=5 determinism re-verified on the redistributable corpus subset.
- [ ] Distribution-side build_hash published in R2 launch ticket.

## When R2 is unblocked

The Founder lifts the freeze with a written directive on a fresh ticket
(BEAAA-N+M). That ticket converts each unchecked box above into a child
issue with its assignee. The R1 shelf build is unchanged at that point;
R2 stacks additively on R1.
