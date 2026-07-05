-- BEAAA-11876 fired-probe canary ledger — hash-chain columns + durable loader.
--
-- Follow-on to 0001_fired_probe_ledger_v1.sql (BEAAA-11872). 0001 made the
-- fired-probe ledger append-only and per-record tamper-evident
-- (preimage_sha256) at both the JSONL runtime sink and this durable Postgres
-- book of record. It did NOT hash-chain the records, so a whole-line
-- delete/reorder at the JSONL layer was undetectable, and no JSONL->Postgres
-- loader existed to make this table the authoritative, complete copy.
--
-- This migration is additive (nullable columns + a partial unique index) so it
-- applies to a live 0001 table without rewriting existing rows:
--
--   * prev_sha256  — the chain head this record links from (genesis for the
--                    first record: sha256:0000...0000).
--   * chain_sha256 — sha256(canonical({prev_sha256, preimage_sha256})); the
--                    running chain value. Byte-identical to the JSONL sink's
--                    chain value so the loader/reconciler joins the two
--                    substrates on it (see
--                    scanners/canary_anti_gaming.py::_fired_probe_chain_link and
--                    scanners/fired_probe_ledger_loader.py).
--
-- The unique index on chain_sha256 makes the JSONL->Postgres load idempotent
-- (re-draining the same lines inserts nothing new) and rejects duplicated
-- records at the durable layer.
--
-- Still additive anti-gaming sidecar infrastructure under
-- are-scanner-api-v1@v1.9.0: no BackBond Score math, rubric weight, judge
-- model, or /scanner/v1/scans response shape changes. The canary ledger is an
-- Underwriting-review signal only and never feeds scoring
-- (scanners/canary_anti_gaming.py module docstring). Strictly INCREASES tamper
-- resistance.

ALTER TABLE canary.fired_probe_records
    ADD COLUMN IF NOT EXISTS prev_sha256 text
        CHECK (prev_sha256 IS NULL OR prev_sha256 ~ '^sha256:[0-9a-f]{64}$'),
    ADD COLUMN IF NOT EXISTS chain_sha256 text
        CHECK (chain_sha256 IS NULL OR chain_sha256 ~ '^sha256:[0-9a-f]{64}$');

-- Idempotent load + whole-record dedup guard. Partial so legacy pre-chain rows
-- (chain_sha256 IS NULL) do not collide.
CREATE UNIQUE INDEX IF NOT EXISTS ux_fired_probe_records_chain
    ON canary.fired_probe_records (chain_sha256)
    WHERE chain_sha256 IS NOT NULL;
