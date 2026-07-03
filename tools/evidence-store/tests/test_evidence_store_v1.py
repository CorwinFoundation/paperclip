"""BEAAA-11366 / BEAAA-11369 Evidence Store v1 migration contract tests.

These tests lock the Postgres DDL invariants in the absence of a local
Postgres service in the agent runtime. If ``EVIDENCE_STORE_PG_DSN`` is set,
the suite also executes behavioral checks against a real Postgres database.
They do not import scanner scoring, rubric, judge, or API response code.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0001_evidence_store_v1.sql"
SQL = MIGRATION.read_text(encoding="utf-8")


BOOK_OF_RECORD_TABLES = {
    "retention_config",
    "signed_batch_manifests",
    "structured_event_summaries",
    "evidence_content_hashes",
}


def test_migration_defines_required_book_of_record_tables():
    for table in BOOK_OF_RECORD_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS evidence_store.{table}" in SQL


def test_signed_manifest_table_carries_runtime_identity_and_metadata():
    required_columns = {
        "tenant_id_hash",
        "tenant_identity",
        "agent_id",
        "agent_version",
        "scanner_version",
        "corpus_version",
        "probe_set_version",
        "collector_version",
        "signed_manifest",
        "manifest_signature_algorithm",
        "manifest_signature",
        "manifest_content_sha256",
        "consent_metadata",
        "redaction_metadata",
        "retention_policy_key",
    }
    for column in required_columns:
        assert column in SQL


def test_corrections_are_new_records_not_overwrites():
    assert "supersedes_evidence_batch_id" in SQL
    assert "supersedes_evidence_event_id" in SQL
    assert "correction_reason" in SQL
    assert "latest_signed_batch_manifests" in SQL
    assert "latest_structured_event_summaries" in SQL
    assert "WHERE correction.supersedes_evidence_batch_id = batch.evidence_batch_id" in SQL
    assert "WHERE correction.supersedes_evidence_event_id = event.evidence_event_id" in SQL


def test_exposure_metering_views_are_read_only_latest_event_projections():
    assert "CREATE OR REPLACE VIEW evidence_store.exposure_meterable_events_v1" in SQL
    assert "CREATE OR REPLACE VIEW evidence_store.exposure_metering_v1" in SQL
    assert "FROM evidence_store.latest_structured_event_summaries AS event" in SQL
    assert "event.tenant_id_hash AS tenant_id" in SQL
    assert "event.agent_id" in SQL
    assert "date_trunc('month', event.event_timestamp)::date" in SQL
    assert "event.summary #>> '{attributes,backbond.evidence.action_class}'" in SQL


def test_exposure_metering_reconciles_counts_to_source_evidence_rows():
    assert "count(*)::bigint AS covered_action_count" in SQL
    assert "count(*)::bigint AS source_evidence_row_count" in SQL
    assert "array_agg(evidence_event_id ORDER BY event_timestamp, evidence_event_id)" in SQL
    assert "GROUP BY\n        tenant_id,\n        agent_id,\n        action_class," in SQL
    assert "COALESCE(counts.evidence_event_ids, ARRAY[]::uuid[]) AS evidence_event_ids" in SQL


def test_exposure_metering_marks_gap_periods_as_unmetered_not_zero():
    assert "generate_series(" in SQL
    assert "bounds.first_period" in SQL
    assert "bounds.last_period" in SQL
    assert "WHEN counts.covered_action_count IS NULL THEN 'unmetered'" in SQL
    assert "ELSE 'metered'" in SQL
    assert "counts.covered_action_count," in SQL
    assert "COALESCE(counts.source_evidence_row_count, 0)::bigint" in SQL


def test_batch_supersession_enforces_tenant_agent_version_lineage():
    assert "validate_signed_batch_supersession" in SQL
    assert "evidence_store_validate_signed_batch_supersession" in SQL
    assert "NEW.tenant_id_hash IS DISTINCT FROM superseded.tenant_id_hash" in SQL
    assert "NEW.agent_id IS DISTINCT FROM superseded.agent_id" in SQL
    assert "NEW.agent_version IS DISTINCT FROM superseded.agent_version" in SQL
    assert "evidence_store_batch_supersession_lineage_mismatch" in SQL


def test_event_supersession_enforces_batch_and_event_lineage():
    assert "validate_structured_event_lineage" in SQL
    assert "evidence_store_validate_structured_event_lineage" in SQL
    assert "NEW.tenant_id_hash IS DISTINCT FROM parent_batch.tenant_id_hash" in SQL
    assert "NEW.agent_id IS DISTINCT FROM parent_batch.agent_id" in SQL
    assert "NEW.agent_version IS DISTINCT FROM parent_batch.agent_version" in SQL
    assert "evidence_store_event_batch_lineage_mismatch" in SQL
    assert "NEW.tenant_id_hash IS DISTINCT FROM superseded_event.tenant_id_hash" in SQL
    assert "NEW.agent_id IS DISTINCT FROM superseded_event.agent_id" in SQL
    assert "NEW.agent_version IS DISTINCT FROM superseded_event.agent_version" in SQL
    assert "NEW.evidence_batch_id IS DISTINCT FROM superseded_event.evidence_batch_id" in SQL
    assert "evidence_store_event_supersession_lineage_mismatch" in SQL


def test_supersession_chains_have_one_direct_correction_per_row():
    assert "ux_evidence_batches_one_direct_correction" in SQL
    assert "ux_evidence_events_one_direct_correction" in SQL
    assert "WHERE supersedes_evidence_batch_id IS NOT NULL" in SQL
    assert "WHERE supersedes_evidence_event_id IS NOT NULL" in SQL
    assert "evidence_store_batch_supersession_multiple_children_forbidden" in SQL
    assert "evidence_store_event_supersession_multiple_children_forbidden" in SQL


def test_every_book_of_record_table_rejects_update_and_delete():
    for table in BOOK_OF_RECORD_TABLES:
        assert f"BEFORE UPDATE ON evidence_store.{table}" in SQL
        assert f"BEFORE DELETE ON evidence_store.{table}" in SQL
    assert "evidence_store_append_only_%_forbidden" in SQL
    assert "integrity_constraint_violation" in SQL


def test_retention_configuration_is_explicit_counsel_pending_stub():
    assert "runtime_evidence_counsel_pending_v1" in SQL
    assert "pending_counsel_guidance" in SQL
    assert "retention_period" in SQL
    assert "legal_hold_after" in SQL
    assert "NULL" in SQL


def test_content_hashes_are_sha256_only_and_indexed():
    assert "CHECK (hash_algorithm = 'sha256')" in SQL
    assert "CHECK (content_sha256 ~ '^sha256:[0-9a-f]{64}$')" in SQL
    assert "ix_evidence_content_hashes_sha256" in SQL


def test_live_postgres_supersession_integrity_if_dsn_is_available():
    dsn = os.getenv("EVIDENCE_STORE_PG_DSN")
    if not dsn:
        pytest.skip("EVIDENCE_STORE_PG_DSN not set; SQL invariant tests cover this runtime")

    sql = textwrap.dedent(
        f"""
        \\set ON_ERROR_STOP on
        BEGIN;
        DROP SCHEMA IF EXISTS evidence_store CASCADE;
        \\i {MIGRATION}

        INSERT INTO evidence_store.signed_batch_manifests (
            evidence_batch_id, tenant_id_hash, tenant_identity, agent_id, agent_version,
            scanner_version, corpus_version, probe_set_version, collector_version,
            signed_manifest, manifest_signature_algorithm, manifest_signature,
            manifest_content_sha256, retention_policy_key, received_at
        ) VALUES (
            '00000000-0000-0000-0000-000000000001', 'tenant-a', '{{"tenant":"a"}}',
            'agent-a', 'v1', 'scanner-v1', 'corpus-v1', 'probe-v1', 'collector-v1',
            '{{"batch":1}}', 'EdDSA', 'sig',
            'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'runtime_evidence_counsel_pending_v1', '2026-07-03T00:00:00Z'
        );

        DO $$
        BEGIN
            INSERT INTO evidence_store.signed_batch_manifests (
                tenant_id_hash, tenant_identity, agent_id, agent_version,
                scanner_version, corpus_version, probe_set_version, collector_version,
                signed_manifest, manifest_signature_algorithm, manifest_signature,
                manifest_content_sha256, retention_policy_key, supersedes_evidence_batch_id,
                correction_reason, received_at
            ) VALUES (
                'tenant-b', '{{"tenant":"b"}}', 'agent-a', 'v1',
                'scanner-v1', 'corpus-v1', 'probe-v1', 'collector-v1',
                '{{"batch":2}}', 'EdDSA', 'sig',
                'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                'runtime_evidence_counsel_pending_v1',
                '00000000-0000-0000-0000-000000000001', 'bad tenant',
                '2026-07-03T00:01:00Z'
            );
            RAISE EXCEPTION 'cross-tenant batch correction unexpectedly accepted';
        EXCEPTION WHEN integrity_constraint_violation THEN
            NULL;
        END $$;

        INSERT INTO evidence_store.signed_batch_manifests (
            evidence_batch_id, tenant_id_hash, tenant_identity, agent_id, agent_version,
            scanner_version, corpus_version, probe_set_version, collector_version,
            signed_manifest, manifest_signature_algorithm, manifest_signature,
            manifest_content_sha256, retention_policy_key, supersedes_evidence_batch_id,
            correction_reason, received_at
        ) VALUES (
            '00000000-0000-0000-0000-000000000002', 'tenant-a', '{{"tenant":"a"}}',
            'agent-a', 'v1', 'scanner-v1', 'corpus-v1', 'probe-v1', 'collector-v1',
            '{{"batch":2}}', 'EdDSA', 'sig',
            'sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
            'runtime_evidence_counsel_pending_v1',
            '00000000-0000-0000-0000-000000000001', 'valid correction',
            '2026-07-03T00:02:00Z'
        );

        DO $$
        BEGIN
            INSERT INTO evidence_store.signed_batch_manifests (
                tenant_id_hash, tenant_identity, agent_id, agent_version,
                scanner_version, corpus_version, probe_set_version, collector_version,
                signed_manifest, manifest_signature_algorithm, manifest_signature,
                manifest_content_sha256, retention_policy_key, supersedes_evidence_batch_id,
                correction_reason, received_at
            ) VALUES (
                'tenant-a', '{{"tenant":"a"}}', 'agent-a', 'v1',
                'scanner-v1', 'corpus-v1', 'probe-v1', 'collector-v1',
                '{{"batch":3}}', 'EdDSA', 'sig',
                'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                'runtime_evidence_counsel_pending_v1',
                '00000000-0000-0000-0000-000000000001', 'second sibling',
                '2026-07-03T00:03:00Z'
            );
            RAISE EXCEPTION 'sibling batch correction unexpectedly accepted';
        EXCEPTION WHEN integrity_constraint_violation THEN
            NULL;
        END $$;

        INSERT INTO evidence_store.structured_event_summaries (
            evidence_event_id, evidence_batch_id, tenant_id_hash, agent_id, agent_version,
            collector_version, event_kind, event_timestamp, rubric_point_id, summary,
            content_sha256
        ) VALUES (
            '10000000-0000-0000-0000-000000000001',
            '00000000-0000-0000-0000-000000000002',
            'tenant-a', 'agent-a', 'v1', 'collector-v1', 'tool_call',
            '2026-07-03T00:04:00Z', 'P4.1', '{{"event":1}}',
            'sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
        );

        INSERT INTO evidence_store.structured_event_summaries (
            evidence_event_id, evidence_batch_id, tenant_id_hash, agent_id, agent_version,
            collector_version, event_kind, event_timestamp, rubric_point_id, summary,
            content_sha256
        ) VALUES (
            '10000000-0000-0000-0000-000000000002',
            '00000000-0000-0000-0000-000000000002',
            'tenant-a', 'agent-a', 'v1', 'collector-v1', 'tool_call',
            '2026-09-03T00:04:00Z', 'P4.1',
            '{{"attributes":{{"backbond.evidence.action_class":"transaction"}}}}',
            'sha256:1111111111111111111111111111111111111111111111111111111111111111'
        );

        DO $$
        BEGIN
            INSERT INTO evidence_store.structured_event_summaries (
                evidence_batch_id, tenant_id_hash, agent_id, agent_version,
                collector_version, event_kind, event_timestamp, rubric_point_id, summary,
                content_sha256, supersedes_evidence_event_id, correction_reason
            ) VALUES (
                '00000000-0000-0000-0000-000000000002',
                'tenant-a', 'agent-b', 'v1', 'collector-v1', 'tool_call',
                '2026-07-03T00:05:00Z', 'P4.1', '{{"event":2}}',
                'sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
                '10000000-0000-0000-0000-000000000001', 'bad agent'
            );
            RAISE EXCEPTION 'cross-agent event correction unexpectedly accepted';
        EXCEPTION WHEN integrity_constraint_violation THEN
            NULL;
        END $$;

        DO $$
        BEGIN
            UPDATE evidence_store.structured_event_summaries
            SET summary = '{{"mutated":true}}'
            WHERE evidence_event_id = '10000000-0000-0000-0000-000000000001';
            RAISE EXCEPTION 'append-only update unexpectedly accepted';
        EXCEPTION WHEN integrity_constraint_violation THEN
            NULL;
        END $$;

        DO $$
        BEGIN
            DELETE FROM evidence_store.signed_batch_manifests
            WHERE evidence_batch_id = '00000000-0000-0000-0000-000000000001';
            RAISE EXCEPTION 'append-only delete unexpectedly accepted';
        EXCEPTION WHEN integrity_constraint_violation THEN
            NULL;
        END $$;

        SELECT count(*) AS latest_batches
        FROM evidence_store.latest_signed_batch_manifests;

        SELECT
            sum(covered_action_count) AS reconciled_count,
            sum(source_evidence_row_count) AS reconciled_source_rows
        FROM evidence_store.exposure_metering_v1
        WHERE metering_status = 'metered';

        SELECT
            metering_status,
            covered_action_count,
            source_evidence_row_count
        FROM evidence_store.exposure_metering_v1
        WHERE accounting_period_start = '2026-08-01'
            AND tenant_id = 'tenant-a'
            AND agent_id = 'agent-a'
            AND action_class = 'transaction';
        ROLLBACK;
        """
    )
    proc = subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1"],
        input=sql,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "latest_batches" in proc.stdout
    assert "reconciled_count" in proc.stdout
    assert "unmetered" in proc.stdout
