# backbond/growth/scripts

Scripts that produce or check the numbers recorded in `../agent-scan-scoreboard.md`.

`collect_agent_scan_friday.py` reads npm and GitHub public APIs, a configured live page, and an optional owned CDN log. Run `python backbond/growth/scripts/collect_agent_scan_friday.py --help` for its inputs. `--append backbond/growth/agent-scan-scoreboard.md` inserts one deduplicated row in Section 2.

Use `AGENT_SCAN_NPM_PACKAGE=backbond` and `AGENT_SCAN_GITHUB_REPO=CorwinFoundation/are-scanner`. Until our npm package is published, the collector reports npm downloads and version as unavailable. Do not use `agent-scan` or `@backbond/agent-scan`; both currently resolve to third-party packages.

Keep a script here runnable from the repository root and reproducible from sources an agent on this VM can actually reach. If a number can only come from a Founder-workstation file or a service no agent can query, record it as a gap in the scoreboard's Notes column rather than hard-coding it.
