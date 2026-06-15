"""BEAAA-5414 integration tests for the customer-true Track B walker.

Integration-tier smoke that exercises ``customer_agent_probe_run_fn``
end-to-end through ``customer_agent_http_runner_factory`` against a
local HTTP echo server bound to an ephemeral port. AG-B6 N=5 calibration
spread = 0 on the synthetic_v0 fallback path lives at
``scripts/beaaa5414_track_b_calibration.py`` because it requires the
full subprocess fork-spawn lifecycle.
"""
