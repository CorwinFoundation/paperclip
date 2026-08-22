# Direct-production workflow quarantined

Status: **quarantined and non-deployable** under BEAAA-25460 / BEAAA-25461.

The former one-click production path was not governed by the required
exact-candidate release ceremony. It has been removed:

- `.github/workflows/deploy.yml` no longer exists;
- `.github/workflows/scanner-edge-image.yml` is manual-only and does not build
  on pushes to `master`;
- no repository workflow in this path is authorized to copy, activate, or
  roll back a Scanner release on `srv1749248`.

Do not restore the deleted workflow, its repository-secret references, the
automatic image-build trigger, or the self-hosted deployment route from Git
history. Historical details and the read-only incident evidence remain in
Paperclip on BEAAA-25460 and BEAAA-25461.

Any replacement production path requires a separately governed proposal,
independent exact-candidate QA, the protected GitHub review gate, and a new
Founder approval bound to the exact release candidate. This quarantine notice
does not authorize a deployment or promotion.
