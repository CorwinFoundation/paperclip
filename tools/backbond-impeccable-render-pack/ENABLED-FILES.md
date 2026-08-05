# Enabled and disabled files

Enabled runtime files are the single dispatcher in `bin/`, the three reviewed modules in `lib/`, pinned `playwright-core@1.55.0`, and the Chromium 140.0.7339.16 binary supplied by the digest-pinned Playwright image. The Phase-one detector remains a separate unchanged image and pack.

All upstream Impeccable browser injection, live mode, hooks, doctor, provider, remote-fetch, agent dispatch, and publication files are absent. The five Phase-one `remote_fetch_exec` findings remain absent and disabled. No upstream executable file is copied into this image.
