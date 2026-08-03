# BEAAA-20725 tau-bench provenance and license note

BLUF: The provisional pilot uses three BackBond-authored local scenarios and wraps tau-bench's task, tool, and database-end-state evaluation method. It imports no upstream dataset or customer data.

- Upstream repository: `https://github.com/sierra-research/tau-bench`
- Reviewed commit: `59a200c6d575d595120f1cb70fea53cef0632f6b`
- Reviewed files: `LICENSE`, `README.md`, `tau_bench/envs/base.py`, `tau_bench/envs/retail/tasks.py`, `tau_bench/envs/retail/tools/*.py`, `tau_bench/agents/tool_calling_agent.py`, and `tau_bench/run.py`.
- License at the pinned commit: MIT.
- Upstream status note: the repository now labels these task versions outdated and directs new benchmark users to its successor. This pilot intentionally retains the already contracted historical pin and imports no task rows; changing the upstream pin would require separate review.
- Redistribution conclusion: this candidate redistributes no upstream source or task data. It records attribution and the pinned methodology reference. The three scenario instructions, tool mapping, fixtures, and expected states are BackBond-authored and synthetic.
- Data boundary: all tool calls execute against deterministic in-memory BackBond mock adapters. No customer records, live third-party systems, vendor services, or external network calls are used at runtime.

Contract anchor: `are-scanner-api-v1@v1.3.0`. Disposition: report-only. Registry integration remains blocked on BEAAA-20356.
