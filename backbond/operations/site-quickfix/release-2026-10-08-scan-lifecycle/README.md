# Site quickfix: live scan lifecycle controller (BEAAA-39411)

This package starts from the live `https://backbond.ai/scan/` bytes captured on
2026-10-08. It replaces the page-owned start/poll loop with the controller from
`3be42edb4de8c755d1a6e6f3bf52d45107e3d324` while retaining the live form's
serialization, validation, connection proof, credential clearing, cancellation,
and error-summary behavior.

The package is idempotent: deploy the complete `site` directory, never individual
files. This issue prepares the package only; it does not authorize deployment.

```sh
SITE_DIR=backbond/operations/site-quickfix/release-2026-10-08-scan-lifecycle/site && netlify deploy --dir "$SITE_DIR" --prod
```

Captured live byte hashes before modification:

- `scan/index.html`: `e7f4dee8c04231d9101d2725b82fd17f023671443c7914cbc3bac643cf247e60`
- `scan/scan.js`: `d94c3e663c82e8ba99db77df94617e7ab594f212412151bbb01b03af4393ee50`
