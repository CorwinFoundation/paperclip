# Live Edge Image-Parity Run Wrappers

Internal operator notes for BEAAA-13361. This is not a CD path.

Build locally:

```bash
docker build --build-arg GIT_SHA="$(git rev-parse HEAD)" -t backbond-scanner-live:"$(git rev-parse --short HEAD)" .
```

Run with compose:

```bash
IMAGE_TAG="$(git rev-parse --short HEAD)" docker compose -f infra/live-edge/docker-compose.local.yml up
```

State is mounted at `/var/lib/are-scanner`; secrets stay outside the image and
enter through an env file or read-only host mounts. Do not bake
`ARE_BUNDLE_SIGNING_KEY`, admin tokens, connector keys, or `secrets.conf` into
the image.
