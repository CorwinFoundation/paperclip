# Sequence 20 Stage Gate provisioning runbook

**Ticket:** BEAAA-16230 (parent BEAAA-16216; origin: Founder Stage Gate FAIL
attachment `d05e00d8`). **Contract:** `are-scanner-api-v1@v1.1.0` (unchanged).

This runbook is the operator-managed side of the reversible Sequence 20
candidate. It closes gaps 2–5 of the Stage Gate FAIL; gap 1 (edge request-id) is
closed by `infra/edge/nginx/backbond-scanner-edge.conf.template`. It changes **no**
score / API contract / rubric / corpus behaviour and does **not** promote — the
candidate SHA is handed to QA and, if approved, to the Founder for the prod
sign-off (BEAAA-15373 / BEAAA-15400 governance).

## Pins (must match the deployed read-back)

| pin | value |
|---|---|
| scanner_version | `0.5.0` |
| corpus_version | `0.1.0` |
| corpus_snapshot_hash | `sha256:112f3410a75d5f73d840c097054d510a7fcf962950a898452c927ac4ac0d3829` |
| acceptance seed | `42` |
| rollback image | `backbond-scanner-live@sha256:7c4ff251dd0cc4d41b88e9e148094355985dc4a39e7ac4e93e280171c4101121` |

## The five gaps and how the config closes them

| gap | requirement | schema field(s) | validator rejection code |
|---|---|---|---|
| 1 | edge X-Request-ID on ordinary 401/404 + log correlation | *(nginx template — see infra/edge/nginx)* | — |
| 2 | two distinct non-customer / non-`vapi` tenants | `[tenants.a]`, `[tenants.b]` | `tenant_b_missing`, `tenants_not_distinct`, `tenant_vapi_forbidden` |
| 3 | externally routable **HTTPS** acceptance base URL | `edge.external_base_url` | `external_base_url_not_https` |
| 4 | side-effect-free synthetic endpoint, not the scanner itself | `[synthetic_endpoint]` | `synthetic_endpoint_self_referential`, `synthetic_endpoint_not_side_effect_free` |
| — | deployed scanner/corpus/snapshot/**seed** read-back | `[deployed]` | `deployed_seed_missing`, `deployed_readback_missing` |
| 5 | retained rollback commit + repo-qualified OCI digest | `[rollback]` | `rollback_identity_missing` |
| c | no secret **values** — only opaque references | `*_ref` / `secret_ref` | `secret_value_in_config` |

## Operator steps (on `srv1749248`, secrets in the root-only channel)

1. **Copy the template off-repo.** Copy
   `config/stage-gate/sequence-20-acceptance.example.toml` into the root-only
   channel (`/etc/backbond/…`). Never commit a filled-in copy — it stays on the
   box. Only opaque references (`credstore:…`, `file:…`, `env:…`, `vault:…`) name
   secrets; secret **values** live only in `/etc/credstore.encrypted`.

2. **Fill in real read-back values.** Replace every placeholder with the exact
   value read off the box (see the operator kit v0.2 §B read-back commands):
   the two existing acceptance tenant aliases + their token references, the
   existing synthetic endpoint host + credential/secret references, the deployed
   scanner/corpus/snapshot/seed, and the active + rollback commit and
   repository-qualified OCI digests.

3. **Validate + emit the Stage Gate record (non-secret):**

   ```sh
   python scripts/deploy/stage_gate_config.py \
     --config /etc/backbond/sequence-20-acceptance.toml --emit-record
   ```

   Exit `0` prints a complete JSON Stage Gate record with **no secret value**;
   exit `1` prints one `FAIL <code>: <reason>` line per unmet gap. Post only the
   emitted record (or the FAIL codes) to BEAAA-16216 — never the config file.

4. **Confirm the edge carries request-ids** with the operator kit v0.2 §C
   preflight against the real `external_base_url`. With this candidate deployed,
   the ordinary `404`/`401` now returns an opaque `X-Request-ID`, and that id
   appears verbatim in the edge access log.

## Dry run (fixture)

The committed example validates clean and emits the record, so the whole path is
provable without touching the box:

```sh
python scripts/deploy/stage_gate_config.py \
  --config config/stage-gate/sequence-20-acceptance.example.toml --emit-record
```

## Rollback

Additive config + edge template only. Rollback = restore the current nginx
config and re-activate the Sequence 20 image
`backbond-scanner-live@sha256:7c4ff251…`. No production change is authorized under
this issue.
