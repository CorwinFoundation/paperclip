# Canonical GHCR image for Scanner commit c4a58caa

Decision: the canonical repository reference for Scanner commit
`c4a58caac801d24e7efa2436dbc5c4086b05d5fe` is:

```text
ghcr.io/corwinfoundation/are-scanner@sha256:b9bd0e70943424331c67c07ad17682412871d4cddde69d97193f847cd0fccfdd
```

This is the image built by the `Scanner Immutable GHCR Image` workflow in
GitHub Actions run `32010676498` and published under tag
`sha-c4a58caac801d24e7efa2436dbc5c4086b05d5fe`. The run checked out the exact
commit, validated the OCI source and revision labels, verified the embedded
release identity, checked runtime imports, and completed successfully. Its
embedded manifest records Scanner `0.5.0`, corpus `0.2.0`, the full build SHA,
and release tag `sha-c4a58caac801d24e7efa2436dbc5c4086b05d5fe`.

The alternate reference
`ghcr.io/corwinfoundation/are-scanner@sha256:f014212dca8f70b73eb70ec9c993e709f81a0d9f1675fcff92b7458f619a7a8d`
is the pre-existing deployment-host archive imported to GHCR. It remains
published as immutable evidence. It is not the canonical promotion input
because its build did not run inside the publishing pipeline.

## Image comparison

The two images are not byte-identical. Their Docker configs and final four
layer digests differ; the four base-image layer digests are identical. A
file-content comparison of exported root filesystems found the same 13,114
entries and no path additions or removals:

- 10,666 entries were byte-identical.
- 2,446 compiled Python files differed only in their 16-byte `.pyc` header;
  every byte after the header matched.
- `/etc/group-` differed only in the password-field marker (`x` versus `*`).
- `/etc/shadow` differed only in the `scanner` account last-change day.

No `/app` file differed. The embedded release manifest and
`.backbond-source-commit` were byte-identical. The images therefore contain the
same Scanner application payload; their different repository digests arise
from build-generated metadata and system-account bookkeeping.

Comparison evidence:

- GitHub artifact `scanner-immutable-image-c4a58caac801d24e7efa2436dbc5c4086b05d5fe`
  (`artifact_id 9281655326`) had ZIP SHA-256
  `75696607d4fce58ab707919f0a48dc5c176d36047fe34fb0845d3042b9669d82`.
- Its Docker archive SHA-256 was
  `0ee87a4be6f4948221a21dffc87500a1037f3e42e2cfa04a89a331fd21d93c2b`.
- The deployment-host archive recorded for the alternate image had SHA-256
  `a5460c36cc50a47da0f80acf58a58821364abd1eea06ba0510d64b6162bea2e8`.

## Operational effect

`scripts/deploy/scanner-deploy.sh` remains a Development Mode helper that takes
five validated identity arguments. It now fails closed when commit `c4a58caa`
is paired with any repository digest other than the canonical reference above.
This record does not promote, activate, retag, or delete either image. A future
shared or production promotion still requires approval for its exact immutable
code, image, and configuration candidate.
