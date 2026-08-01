# BackBond drop-in insurance appliance

Install one thing. Grant it read access. It reads your agents' activity where it
already runs, analyzes it in place, and sends BackBond **only findings** — never
your data. "Plug it in and get insured."

This is the reference implementation of the appliance described in Addendum C
(the Listening Home) and the Enso design-partner brief. It is vendor-agnostic:
Enso is the first substrate; other agent environments are added as readers.

## What the partner does (the entire ask — three things)

1. **Run one installer** (`install.sh`, or run the container).
2. **Grant read access** to where the agent activity already lives (a log dir, an
   NDJSON file, the engine's activity output). No code changes, no instrumentation.
3. **Sign a metadata-only data arrangement** with BackBond.

That's it. Everything else is BackBond's.

## What leaves the host (and what never does)

Leaves: action counts, action classes, peril **determinations**, and an
**evidence-commitment hash** per event — all Ed25519-signed and hash-chained.

Never leaves: post text, messages, recipients, transcripts, CRM records, PII,
audio — any content. Content is read **only in memory, on your host**, for local
rule evaluation, then dropped. This is enforced as a fail-closed invariant
(`selftest`), not a policy: an event that cannot be reduced to no-content is
dropped, not shipped.

## Quick start

```bash
./install.sh                      # installs deps, self-tests the no-content invariant, prints enrollment
# edit config.yaml -> set reader.path
python3 -m appliance.main run -c config.yaml --dry-run
python3 -m appliance.main run -c config.yaml
```

Or containerized:

```bash
docker build -t backbond-appliance .
docker run --rm \
  -v /var/lib/enso/activity:/data:ro \        # substrate, READ-ONLY
  -v $PWD/config:/config:ro \
  -v backbond-state:/home/backbond/.backbond \
  backbond-appliance run
```

## Commands

- `selftest` — prove the no-content invariant against a content-laden fixture (run this first).
- `enroll` — print the appliance's enrollment record (id + public key) to register with BackBond.
- `run` — read → analyze locally → emit signed no-content batches (to outbox or HTTP).

## How it works (pipeline)

```
substrate ──reader──▶ normalize ──▶ [local rule eval on content] ──▶ redact (fail-closed no-content)
                                                │                              │
                                                ▼                              ▼
                                          determinations                emittable events
                                                └──────────────┬───────────────┘
                                                               ▼
                                      batch: Ed25519 sign + hash-chain + idempotency
                                                               ▼
                                                   ship (outbox / HTTP)  ──▶ BackBond
```

- **Readers** (`appliance/readers/`) are the only components that touch content, and
  only on the partner host. Add a reader to support a new agent environment.
- **Rules** (`appliance/rules/`) are vetted BackBond packs (CEL in production; a safe
  declarative DSL here). Clients select packs; they do not author production rules.
- **Trust core** (`appliance/batch.py`) is the reused BackBond Collector: signing,
  chaining, idempotency, producer pins — so BackBond can trust batches it did not see created.

## Evidence tier

A log-inferred appliance sees what is logged. It sits at a **middle evidence tier**
(better than self-report, below instrumented or kernel-level). BackBond assigns the
tier from the verified substrate; it is not client-declared. The tier drives pricing.

## Claims without custody

BackBond holds the **evidence-commitment hash**, never the content. The partner
retains the sealed evidence and produces it under chain-of-custody only when a claim
triggers, verified against the stored hash. BackBond never holds content in steady
state.

## Testing the full loop (BackBond-side ingest server)

`server/ingest_server.py` is the receiving end — run it on your machine to exercise
the complete round trip: appliance --HTTP--> server --verify--> accept. The server
INDEPENDENTLY enforces bearer auth, Ed25519 signature, recomputed batch_hash,
hash-chain continuity, idempotency, and the no-content boundary (so a compromised or
outdated appliance cannot sneak content past it).

```bash
# terminal 1 — start the receiver
BACKBOND_INGEST_TOKEN=secret python3 -m server.ingest_server --port 8787

# terminal 2 — point the appliance at it and ship
#   config.yaml: ship.mode: http, url: http://127.0.0.1:8787/ingest
BACKBOND_SHIP_TOKEN=secret python3 -m appliance.main run -c config.yaml
curl -s http://127.0.0.1:8787/status | python3 -m json.tool   # findings by rule/tenant/severity
```

## Production hardening (P0 — complete)

- **CEL rule engine** (`appliance/rules/*.cel.yaml`) is the production rule format;
  the declarative packs remain and are parity-tested identical.
- **Batch windowing** (`ship.max_events_per_batch`) splits large runs into
  independently signed, hash-chained batches — no more single oversized batch.
- **Contract-v1.0 aligned** batch fields (`batch_id`, `counts.{billable_actions,tokens}`,
  `gen_ai.operation.name`, producer pin set).
- **Hardened HTTP shipper** — gzip, bearer auth from `BACKBOND_SHIP_TOKEN`, bounded
  retries with exponential backoff, idempotent redelivery (server dedupes by hash).
- **Allowlist layer** as first-class, versioned config (CEL), with suppression counts.
- **CI gate** (`run_all_tests.sh`, `.github/workflows/ci.yml`) — every merge must pass
  the no-content invariant, e2e, loop, and parity/windowing/contract suites.

## Status

Production-hardened reference. Four green gates, synthetic data only (no real/partner data):
- `tests/e2e.py` — no-content invariant, signed + chained batches, idempotency, tamper rejection, determinations, tenancy.
- `tests/loop_test.py` — full appliance→server→verify loop; server rejects tamper and validly-signed content-laden batches.
- `tests/parity_test.py` — CEL≡declarative parity, windowing + chain, contract-v1.0 fields.
- `run_all_tests.sh` — runs all of the above as the CI gate.

Run everything: `bash run_all_tests.sh`. See `INSTALL.md` for environment/security posture.
