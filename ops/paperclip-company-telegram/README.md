# Paperclip company Telegram bridge

This directory is the durable source for the Founder-only Telegram bridge installed at
`/usr/local/lib/paperclip-company-telegram/`.

Run the narrow regression suite from the repository root:

```sh
python3 -m unittest ops/paperclip-company-telegram/test_bridge.py
```

Promotion is gated on independent QA approval for the exact immutable commit. Install
`bridge.py` and `bridge_entrypoint.py` with root ownership and mode `0755`, then restart
`paperclip-company-telegram.service`. Before replacing files, record their hashes and keep
recoverable copies outside the install directory. Roll back by restoring both prior files,
restarting the service, and rechecking its active state and logs.
