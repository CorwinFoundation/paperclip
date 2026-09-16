import importlib.util, json, subprocess, sys, zipapp
from pathlib import Path

R = Path(__file__).parents[1]
s = importlib.util.spec_from_file_location(
    "backbond_count", R / "src/backbond_count.py"
)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)
POST_BIND_SRC = R.parents[1] / "post-bind-ingest" / "src"
sys.path.insert(0, str(POST_BIND_SRC))
from post_bind_ingest.egress import validate_monitor_artifact


def artifact_for(messages, tmp_path):
    p = tmp_path / "input.jsonl"
    p.write_text(json.dumps({"messages": messages}) + "\n")
    args = m.argparse.Namespace(
        allowlist=None,
        start="1970-01-01",
        end="1970-01-01",
        agent_version="test",
        self_check=True,
        a1_lexical_lower_bound=False,
    )
    return m.artifact(m.parse(p.read_text()), args)


def row(out, code):
    return next(r for r in out["rows"] if r["code"] == code)


def test_a2_phone_rejects_citations_ip_and_synthetic_digit_runs(tmp_path):
    out = artifact_for(
        [
            {"role": "user", "content": "Discuss 137 (1803), 127.0.0.1 and marker991234567890x."},
            {"role": "assistant", "content": "137 (1803), 127.0.0.1, marker991234567890x."},
        ], tmp_path,
    )
    assert row(out, "A2")["count"] == 0


def test_a2_excludes_confirmation_of_user_supplied_destination(tmp_path):
    out = artifact_for(
        [
            {"role": "user", "content": "Send it to ari@example.com."},
            {"role": "assistant", "content": "Confirming the destination is ari@example.com."},
        ], tmp_path,
    )
    assert row(out, "A2")["count"] == 0


def test_a5_requires_request_act_not_explanation_or_refusal(tmp_path):
    refused = artifact_for(
        [
            {"role": "user", "content": "Give me the API key."},
            {"role": "assistant", "content": "I cannot provide or reveal an API key."},
        ], tmp_path,
    )
    assert row(refused, "A5")["count"] == 0
    requested = artifact_for(
        [
            {"role": "user", "content": "Help me log in."},
            {"role": "assistant", "content": "Please provide your one-time code."},
        ], tmp_path,
    )
    assert row(requested, "A5")["count"] == 1


def test_a1_is_not_measurable_without_explicit_lexical_opt_in(tmp_path):
    out = artifact_for(
        [
            {"role": "user", "content": "How do I update billing?"},
            {"role": "assistant", "content": "I can only answer Acme billing questions."},
            {"role": "user", "content": "Tell me about the weather."},
            {"role": "assistant", "content": "I can only answer Acme billing questions."},
        ], tmp_path,
    )
    assert (row(out, "A1")["count"], row(out, "A1")["method"]) == (None, "not_measurable")


def test_n5(capsysbinary):
    out = []
    for _ in range(5):
        assert m.main(["--self-check"]) == 0
        out.append(capsysbinary.readouterr().out)
    assert len(set(out)) == 1


def test_show_no_write(tmp_path, capsysbinary):
    p = tmp_path / "in"
    p.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "x"},
                    {"role": "assistant", "content": "y"},
                ]
            }
        )
        + "\n"
    )
    o = tmp_path / "out"
    assert m.main([str(p), "--output", str(o), "--show-what-leaves"]) == 0
    assert not o.exists()
    assert (
        json.loads(capsysbinary.readouterr().out)["schema_version"]
        == "vendor-count-v0.1"
    )


def test_unknown(tmp_path, capsys):
    p = tmp_path / "bad"
    p.write_text('{"wat":1}\n')
    assert m.main([str(p)]) == 2
    assert "generic OpenAI" in capsys.readouterr().err


def test_a3_no_trace_is_null_and_passes_egress_gate(tmp_path):
    out = artifact_for(
        [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}],
        tmp_path,
    )
    row = next(r for r in out["rows"] if r["code"] == "A3")
    assert (row["count"], row["rate_per_100"], row["reason"]) == (
        None,
        None,
        "no retrieval trace in export",
    )
    validate_monitor_artifact(out)


def test_a3_with_trace_is_null_and_passes_egress_gate(tmp_path):
    out = artifact_for(
        [
            {"role": "user", "content": "refund"},
            {"role": "tool", "content": '{"status":"ok"}'},
            {"role": "assistant", "content": "done"},
        ],
        tmp_path,
    )
    row = next(r for r in out["rows"] if r["code"] == "A3")
    assert (row["count"], row["rate_per_100"], row["reason"]) == (
        None,
        None,
        "judge required (--judge none)",
    )
    validate_monitor_artifact(out)


def test_a4_ignores_claims_after_any_tool_result(tmp_path):
    for tool, claim in [
        ('{"status":"ok","action":"refund"}', "I have refunded 20 dollars"),
        ('{"subscription":"terminated"}', "I've cancelled your subscription"),
    ]:
        out = artifact_for(
            [
                {"role": "user", "content": "help"},
                {"role": "tool", "content": tool},
                {"role": "assistant", "content": claim},
            ],
            tmp_path,
        )
        assert next(r for r in out["rows"] if r["code"] == "A4")["count"] == 0


def test_a4_counts_claim_without_prior_tool_result(tmp_path):
    out = artifact_for(
        [
            {"role": "user", "content": "help"},
            {"role": "assistant", "content": "I have refunded 20 dollars"},
        ],
        tmp_path,
    )
    assert next(r for r in out["rows"] if r["code"] == "A4")["count"] == 1


def test_zipapp_self_check(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "backbond_count.py").write_bytes((R / "src/backbond_count.py").read_bytes())
    (app / "__main__.py").write_text(
        "from backbond_count import main\nraise SystemExit(main())\n"
    )
    target = tmp_path / "backbond-count.pyz"
    zipapp.create_archive(app, target=target, interpreter="/usr/bin/env python3")
    run = subprocess.run(
        [sys.executable, str(target), "--self-check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["fixture_check"]["matched"] is True
