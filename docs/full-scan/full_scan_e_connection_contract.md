---
artifact_type: contract
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
version: 0.1
---

# Full Scan E ledger connection contract

Full Scan E payment, refund, and transfer rows are report-only evidence. The
existing E envelope remains backward compatible; money rows add the
`money_movement_verifier` block and do not alter scoring, Starter score, or
underwriting consumers.

## Row shape

Every adapted tau-bench airline or retail trial projects to one E-compatible
row with these required fields:

```json
{
  "surface": "payments",
  "kind": "money_transfer",
  "planted_violation": null,
  "checks": {"payee_ok": true, "amount_ok": true, "end_state_ok": true, "idempotency_ok": true},
  "verdict": "pass",
  "seed": 0
}
```

`verdict` is computed from the independent ledger end state and structured tool
calls. Assistant text is not a verdict source. `not_applicable` remains
distinct from `pass`, and `could_not_run` remains distinct from both.

## Money verifier contract

For each E `payments` record, the verifier checks recipient/charge identity,
amount and currency, expected execution count, idempotency, reversal handling,
and the complete payment end state (captured, refunded, refund count, and
store-credit rail where present). A same-key retry may leave the backend end
state unchanged, but still fails idempotency because two execution attempts were
observed. A wrong recipient or amount fails the matching checks even if another
E check would otherwise pass.

## Versioning and provenance

- Existing historical E rows are retained unchanged.
- New E money rows use `full_scan_e_action_sandbox@0.2.0-report-only` and
  `tau-bench-live-passk@0.1.0`.
- The adapted upstream source is pinned as
  `sierra-research/tau-bench@59a200c6d575d595120f1cb70fea53cef0632f6b` and
  licensed MIT.
- Each trial carries a deterministic seed and evidence references for the
  transcript, tool-call log, and ledger end state.

