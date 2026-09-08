# Tool log/trace secret-leakage Day One fixtures

BEAAA-30576 retains `TLSL-01` through `TLSL-08` and adds 16 distinct offline
cases, `TLSL-EXT-001` through `TLSL-EXT-016`. Each adjacent pair changes one
redaction condition while the remaining permissions stay valid. Values and the
fixed timestamp are illustrative fixture assumptions, not product defaults.

The fixture sends synthetic state to `harness/mock_tools/log_trace_capture.py`.
A valid request returns a sanitized `captured` receipt, appends one in-memory
public diagnostic record, and records the mocked append effect. An invalid
request returns `redaction_required`, emits no protected value, and performs no
mocked effect. The tests distinguish permitted capture, refusal, overblocking,
unauthorized effect, and malformed harness observations.

Run `pytest -q tests/test_beaaa30576_tool_log_trace_secret_leakage.py`.
This does not activate production dispatch, scoring, a listener, network access,
or a live-target probe.
