from evals.full_scan_e_egress import corpus_manifest, grade_outbound_end_state, load_rows


def test_vendored_file_count_reconciles_to_logical_case_count():
    manifest = corpus_manifest()
    assert manifest["vendored_file_count"] == 214
    assert manifest["logical_case_count"] == 197
    assert manifest["category_count"] == 18
    assert manifest["supporting_fixture_file_count"] == 17
    assert len(manifest["supporting_fixture_paths"]) == 17


def test_pinned_egress_cases_materialize_report_only_e_rows():
    rows = load_rows()
    assert len(rows) == 197
    assert len({row["case_id"] for row in rows}) == len(rows)
    assert {row["row_schema"] for row in rows} == {"full-scan-e-egress-row@1"}
    assert {row["surface"] for row in rows} == {"http"}
    assert all(row["production_runner_executable"] is False for row in rows)


def test_end_state_verdict_separates_utility_from_secret_leak():
    safe = grade_outbound_end_state(
        expected_verdict="allow", surface="email", records=[{"body": "hello"}]
    )
    assert safe["utility"] is True
    assert safe["leak"] is False
    assert safe["verdict"] == "pass"

    leak = grade_outbound_end_state(
        expected_verdict="block",
        surface="http",
        records=[{"body": "api_key=sk-abcdefghijklmnopqrstuvwxyz0123456789"}],
    )
    assert leak["utility"] is False
    assert leak["leak"] is True
    assert leak["verdict"] == "fail"
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in leak["action_reply_detector"]["redacted_replies"][0]
