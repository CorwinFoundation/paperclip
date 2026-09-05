-- BEAAA-29823 unit (a): the binding limb of the ADR BEAAA-20236 option-A
-- reconciliation key (tenant / binding / agent / deployment).
--
-- No wire change. The appliance emits no binding identifier and never has:
-- EMIT_ALLOWLIST carries none through 0.1.3, and verify_batch rebuilds the
-- signed body from nine named keys, none of them a binding. The limb is
-- resolved server-side from the authenticated appliance_id, the same way the
-- tenant limb already is.
--
-- Side tables rather than ALTER TABLE, following the shape BEAAA-24613 used for
-- the agent limb: CREATE TABLE IF NOT EXISTS is idempotent under a migration
-- runner that replays every script on every open, and it lets both columns be a
-- real NOT NULL instead of the nullable placeholder ADR BEAAA-20236 line 74
-- forbids.

-- One row per binding revision. Re-binding appends; it never rewrites history,
-- so a batch whose window opened under the previous binding still resolves to
-- that binding (ADR rule 7: late events are not shifted onto the current one).
CREATE TABLE IF NOT EXISTS appliance_enrollment_bindings (
    appliance_id    TEXT NOT NULL REFERENCES appliance_enrollments(appliance_id),
    binding_id_hash TEXT NOT NULL,
    effective_from  TEXT NOT NULL,
    PRIMARY KEY (appliance_id, effective_from)
);

CREATE TRIGGER IF NOT EXISTS appliance_enrollment_bindings_no_update
BEFORE UPDATE ON appliance_enrollment_bindings BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS appliance_enrollment_bindings_no_delete
BEFORE DELETE ON appliance_enrollment_bindings BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;

-- The resolved limb, pinned per accepted batch. effective_from records which
-- revision was in force at the batch window start, so the resolution is
-- auditable after a later re-binding rather than only re-derivable.
CREATE TABLE IF NOT EXISTS appliance_ingest_receipt_bindings (
    batch_hash      TEXT PRIMARY KEY REFERENCES appliance_ingest_receipts(batch_hash),
    binding_id_hash TEXT NOT NULL,
    effective_from  TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS appliance_receipt_bindings_no_update
BEFORE UPDATE ON appliance_ingest_receipt_bindings BEGIN
    SELECT RAISE(ABORT, 'append_only_update_forbidden');
END;
CREATE TRIGGER IF NOT EXISTS appliance_receipt_bindings_no_delete
BEFORE DELETE ON appliance_ingest_receipt_bindings BEGIN
    SELECT RAISE(ABORT, 'append_only_delete_forbidden');
END;
