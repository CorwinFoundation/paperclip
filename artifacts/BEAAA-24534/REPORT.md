# BEAAA-24534 — structural re-key of the content-boundary histogram exemption

BLUF: the CTO's item 1 is confirmed and fixed. The exemption is now addressed by
the tuple of keys walked to reach the node plus object identity, never by a
rendered path string, so no key name the sender chooses can forge it. Mutant 8
is added and kills. Item 2 is confirmed **not** done — the atomic reject is
byte-identical to `68ed6521` and stays on the tenant limb. Item 3 is written
down in `PRODUCTION-INGEST.md` as a named P1 enrollment invariant and covered by
two tests including the gate-4 case.

Base `68ed6521951cc09d3e6944c708db593b67c6c612`. Nothing merged, promoted,
deployed or released. Synthetic Enso-shaped data only; no network, no Enso
contact.

## Item 1 (P0) — confirmed, reproduced independently, fixed

I reproduced the defect before touching it, with my own harness rather than the
CTO's, driving the real sealed Ed25519 signer and the real `ApplianceIngestStore`:
`artifacts/BEAAA-24534/repro_path_forgery.py`. At `68ed6521`:

```
A. sealed CONTENT_FIELDS: 29 fields, prohibited payload keys ['message', 'recipient'] all members: True
B. genuine verify_batch: True
C. decoy 'counts.by_action_class' (top-level key forging the exempt dotted path)
   unsigned key still verifies: True
   content boundary: PASSED -- LEAK
C. decoy '$.counts.by_action_class' (top-level key forging the exempt path including the root)
   unsigned key still verifies: True
   content boundary: rejected: content_boundary_violated:$.$.counts.by_action_class.message
C. decoy 'extra' (control: an ordinary key, must always be caught)
   unsigned key still verifies: True
   content boundary: rejected: content_boundary_violated:$.extra.message

D. leaks: 1
```

The same harness at the repaired SHA:

```
C. decoy 'counts.by_action_class' (top-level key forging the exempt dotted path)
   unsigned key still verifies: True
   content boundary: rejected: content_boundary_violated:$['counts.by_action_class'].message
C. decoy '$.counts.by_action_class' ...
   content boundary: rejected: content_boundary_violated:$['$.counts.by_action_class'].message
C. decoy 'extra' (control) ...
   content boundary: rejected: content_boundary_violated:$.extra.message

D. leaks: 0
```

Line F now names a violation path instead of `PASSED`, which is the CTO's
stated done-condition. The two non-forging decoys are the non-vacuity control:
they were caught before the fix and are still caught after it, so the change is
not a blanket "reject everything".

### The change

`tools/log-tracker/src/log_tracker/appliance_ingest.py`:

- `_find_prohibited` now carries `keys: tuple` — the actual keys and list
  indices walked — instead of a concatenated `path: str`. The exemption is
  `keys == ("counts", "by_action_class")`, which no key name can forge, **and**
  `value is exempt`, where `exempt` is the object resolved once up front by the
  new `_histogram_node(batch)` from `batch["counts"]["by_action_class"]`. Both
  of the CTO's two suggested constructions, together; either alone would have
  been sufficient.
- `exempt` defaults to `None`, which exempts nothing. A caller that forgets the
  argument therefore rejects genuine batches loudly rather than silently
  reopening the boundary — the failure direction is fail-closed.
- `_render_path` renders the walked tuple, and is used **only** to build the
  rejection reason. No control decision reads it. A key that would otherwise
  render ambiguously against a genuine address is bracket-quoted, so
  `content_boundary_violated:$['counts.by_action_class'].message` can never be
  confused with the real `$.counts.by_action_class`.
- The exact-equality check in `_histogram_violation` is untouched, as ruled.
- One call site, at `appliance_ingest.py:232`. `grep -rn '_find_prohibited\|ACTION_CLASS_HISTOGRAM'`
  over the tree returns that file and nothing else.

### Mutation table, re-run in full

Each mutation was applied to the repaired tree, the suite re-run, and the tree
restored; every mutation was asserted to have actually changed the file, so a
no-op sed cannot report as a survivor. Runner: `/tmp/mutate.py`, reproduced
inline in the "Mutants" appendix below.

| Mutant | Result |
| --- | --- |
| M1 `FIRST_BATCH_SEQ = 0` (revert F2) | 13 failed |
| M2 histogram exemption removed (revert F1) | 24 failed |
| M3 `_histogram_violation` disabled | 5 failed |
| M4 `batch_seq` back to `int()` (revert F6) | 7 failed |
| M5 counts back to `int()` (revert F6) | 3 failed |
| M6 `scoped_status` ignores token/tenant (revert F5) | 5 failed |
| M7 loader returns a stub verifier and a 1-field set (round-1 blindness) | 10 failed |
| **M8 exemption re-keyed to the rendered path string (revert this repair)** | **3 failed** |

M8 is the mutant the previous seven could not have caught. The three tests it
kills are exactly the three that address the mechanism rather than the
histogram's contents:
`test_a_dotted_top_level_key_cannot_forge_the_histogram_exemption[counts.by_action_class]`,
`test_the_forgery_is_rejected_even_when_it_rides_a_delivered_batchs_hash`, and
the `content_boundary_violated` case of
`test_a_rejected_envelope_mutates_no_other_appliances_ledger`. The other two
parametrisations of the forgery test survive M8, correctly — they were never
the hole.

### New tests

All in `tools/log-tracker/tests/test_appliance_ingest_beaaa20231.py`, which is
already wired into the `passive-monitor-demo` workflow, so CI runs them.

- `test_a_dotted_top_level_key_cannot_forge_the_histogram_exemption` —
  parametrised over the forging key, the root-marker near-miss, and an ordinary
  key as control. Asserts `verify_batch(...) is True` first, so the test proves
  the injected key really does ride outside the signature; then asserts the
  rejection, that the reason names the forged key and not the genuine address,
  and that nothing was written.
- `test_the_forgery_is_rejected_even_when_it_rides_a_delivered_batchs_hash` —
  QA's second consequence on BEAAA-24538: because the injected key is outside
  `batch_hash`, the forgery shares a delivered batch's idempotency key. The
  boundary runs before the duplicate lookup, so it is still refused.
- `test_only_the_top_level_histogram_is_exempt_not_a_lookalike_deeper_in` — the
  exemption is one structural address, not a shape; a `counts.by_action_class`
  nested inside an event is scanned like anything else.

## Item 2 (P0) — confirmed NOT done

The atomic reject is unchanged from `68ed6521`:

```python
for item in [*events, *determinations]:
    if item.get("tenant_id") != tenant_id:
        raise IngestRejected("unauthorized_scope")
```

`diff` of that region against `68ed6521` is empty, and
`grep -n 'agent_id\|deployment_id\|policy_number'` over the adapter returns
nothing. I accept the correction: my "rule-execution, no decision needed"
classification was wrong, and the widening would have rejected the normal
multi-agent envelope.

New regression guard so nobody re-introduces it:
`test_a_genuine_multi_agent_envelope_is_accepted` builds one genuine sealed
envelope spanning agents `ag1` and `ag2` under a single tenant, asserts the
envelope really does span two `agent_id`s, and asserts it ingests. Widening the
reject to the agent limb fails this test immediately.

## Item 3 (P1) — documented and tested

`ops/paperclip/numbat/PRODUCTION-INGEST.md` gains a **"P1 enrollment invariant
(single-tenant appliance)"** section stating the invariant by name: an
`appliance_id` is enrolled for exactly one `tenant_id` for the life of the
enrollment row, enforced by `enroll(...)` refusing any rebinding with
`enrollment_binding_conflict` while remaining idempotent for restarts. It
records that this — not any wire-level check — is what makes ADR BEAAA-20236
option A's "mixed-tenant batches are structurally impossible" true of P1, and
carries the A0 ruling, the tenant-limb-only scope with its reasoning, and why
receiver-side splitting is not approved for P1. A second section documents the
content boundary and why the one exemption is addressed structurally.

Gate-4 test: `test_a_rejected_envelope_mutates_no_other_appliances_ledger`
enrolls two appliances on different tenants, lands a good batch for the first,
snapshots the receipts table, then puts a rejected envelope through the second
by four different routes — content boundary, tenant scope, malformed seq,
broken chain — and asserts after each that the whole receipts table is
byte-identical to the snapshot and that the rejected appliance has no rows.
`test_enrollment_is_the_p1_single_tenant_invariant` covers the invariant itself.

## Verification

- `tools/log-tracker/tests`: **74 passed** (63 before; +11 new).
- `test_appliance_ingest_beaaa20231.py` alone: 37 passed (26 before).
- Mutation: M1–M8 all killed, table above.
- `artifacts/BEAAA-24534/repro_path_forgery.py`: exit 0 at the repaired SHA,
  exit 1 at `68ed6521`.

## Scope and classification

Still a **rule change** to the content-boundary control, so still not
self-certified: this candidate is what the CTO said they would co-sign. The
change strictly narrows the exemption — the set of nodes skipped by the name
scan goes from "any node whose rendered path collides with the literal" to
"exactly the object `batch["counts"]["by_action_class"]`". No node that was
scanned before is unscanned now.

No scoring surface is touched. Changed paths are `tools/log-tracker/**`,
`ops/paperclip/numbat/PRODUCTION-INGEST.md` and `artifacts/**` — disjoint from
the scanner, rubric, corpus and judge paths, so the N=5 golden-set delta is 0 by
construction rather than by sampling. The sealed appliance under
`ops/paperclip/numbat/backbond-appliance-0.1.0/` is unmodified.

Out of scope, as directed: the receipt row carrying no agent or binding
identifier, so the ledger cannot reproduce the ADR's reconciliation output list.
Filed separately as BEAAA-24613; not folded into this candidate.

## Mutants

```python
M1  FIRST_BATCH_SEQ = 1                                     -> FIRST_BATCH_SEQ = 0
M2  the two-line exemption block in _find_prohibited        -> deleted
M3  body of _histogram_violation                            -> return None
M4  _require_seq guard                                      -> return int(value)
M5  _require_count guard                                    -> return int(counts.get(key, 0) or 0)
M6  scoped_status token check + tenant filter               -> return self.status()
M7  load_sealed_contract return                             -> (lambda b, k: True), {"content"}
M8  keys == ACTION_CLASS_HISTOGRAM_KEYS and value is exempt -> rendered-path string equality
```
