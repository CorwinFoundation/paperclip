#!/usr/bin/env bash
# Container-only installer/upgrade/rollback control. Host Python is never used.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="${BACKBOND_APPLIANCE_STATE_DIR:-$HERE/.release-state}"
STATE_FILE="$STATE_DIR/releases.env"
ACTION="${1:-verify}"

fail() {
  local check fix
  case "$1" in
    001) check='run: docker version'; fix='install or upgrade Docker Engine to version 24 or newer' ;;
    002) check='run: docker compose version'; fix='install the Docker Compose v2 plugin' ;;
    003) check='run: command -v sha256sum'; fix='install the host coreutils package that provides sha256sum' ;;
    004) check='run: (cd <appliance-dir> && sha256sum -c release/SHA256SUMS && sha256sum -c release/MANIFEST.sha256)'; fix='replace the package with an intact copy from the approved release source' ;;
    005) check='run: gzip -t release/backbond-appliance-image.tar.gz'; fix='replace the package, then retry the install after the archive verifies' ;;
    006) check='run: docker image inspect <image-ref>'; fix='remove the incomplete local image and run the install again' ;;
    007) check='compare release/IMAGE_ID with: docker image inspect --format {{.Id}} <image-ref>'; fix='remove the mismatched local image and reinstall from the verified package' ;;
    008) check='run: test -f .env && printf present'; fix='run ./install.sh install before starting the appliance' ;;
    009) check='run: test -f "${BACKBOND_APPLIANCE_STATE_DIR:-<appliance-dir>/.release-state}/releases.env"'; fix='complete an install or upgrade before requesting rollback' ;;
    010) check='inspect the redacted CURRENT_IMAGE_ID and PREVIOUS_IMAGE_ID names in releases.env'; fix='complete one successful upgrade before requesting rollback' ;;
    011) check='run: docker image inspect <previous-image-id>'; fix='restore the prior immutable image locally, then retry rollback' ;;
    012) check='run: ./install.sh verify|install|upgrade|start|stop|status|rollback|uninstall'; fix='rerun with one supported action' ;;
    013) check='compare .env with the generated values recorded in releases.env'; fix='restore .env from a successful install or upgrade; do not edit it by hand' ;;
    014) check='remove compose.override.yaml, compose.override.yml, and docker-compose override files'; fix='use only the manifested compose.yaml supplied in the approved package' ;;
    015) check='compare BACKBOND_APPLIANCE_IMAGE, BACKBOND_SUBSTRATE_PATH, and BACKBOND_CONFIG_PATH with releases.env'; fix='remove post-install environment overrides or rerun install with the intended bind sources' ;;
    *) check='record the stable error code shown above'; fix='stop and request operator support before retrying' ;;
  esac
  printf 'ERROR BB-APPL-%s %s\n' "$1" "$2" >&2
  printf 'SAFE  appliance state was not changed\n' >&2
  printf 'CHECK %s\n' "$check" >&2
  printf 'FIX   %s\n' "$fix" >&2
  printf 'HELP  collect: appliance version, OCI digest, error code, and redacted logs\n' >&2
  exit 1
}

require_runtime() {
  command -v docker >/dev/null 2>&1 || fail 001 'Docker 24 or newer is required'
  docker compose version >/dev/null 2>&1 || fail 002 'Docker Compose v2 is required'
}

verify_bundle() {
  local manifest_output failed_files
  command -v sha256sum >/dev/null 2>&1 || fail 003 'sha256sum is required'
  (cd "$HERE" && sha256sum -c release/SHA256SUMS) \
    || fail 004 'release checksum verification failed'
  if ! manifest_output="$(cd "$HERE" && sha256sum -c release/MANIFEST.sha256 2>&1)"; then
    failed_files="$(printf '%s\n' "$manifest_output" | sed -n 's/: FAILED$//p' | paste -sd ',' -)"
    [ -n "$failed_files" ] || failed_files='unknown file'
    fail 004 "release manifest verification failed: $failed_files"
  fi
  if find "$HERE" -maxdepth 1 -type f \( \
      -name 'compose.override.yaml' -o -name 'compose.override.yml' -o \
      -name 'docker-compose.yaml' -o -name 'docker-compose.yml' -o \
      -name 'docker-compose.override.yaml' -o -name 'docker-compose.override.yml' \
    \) -print -quit | grep -q .; then
    fail 014 'an unmanifested Compose override file is present'
  fi
  printf 'PASS  artifact checksums verified\n'
}

load_image() {
  local image_ref expected_id actual_id
  image_ref="$(cat "$HERE/release/IMAGE_REF")"
  expected_id="$(cat "$HERE/release/IMAGE_ID")"
  gzip -dc "$HERE/release/backbond-appliance-image.tar.gz" | docker image load >/dev/null \
    || fail 005 'OCI image load failed'
  actual_id="$(docker image inspect --format '{{.Id}}' "$image_ref" 2>/dev/null)" \
    || fail 006 'loaded OCI image cannot be inspected'
  [ "$actual_id" = "$expected_id" ] \
    || fail 007 "loaded image identity mismatch (expected $expected_id)"
  printf '%s\n' "$actual_id"
}

write_state() {
  local new_id="$1" substrate_path="$2" config_path="$3" old_current=""
  mkdir -p "$STATE_DIR"
  chmod 0700 "$STATE_DIR"
  if [ -f "$STATE_FILE" ]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE"
    old_current="${CURRENT_IMAGE_ID:-}"
  fi
  umask 077
  {
    printf 'CURRENT_IMAGE_ID=%q\n' "$new_id"
    printf 'PREVIOUS_IMAGE_ID=%q\n' "$old_current"
    printf 'SUBSTRATE_PATH=%q\n' "$substrate_path"
    printf 'CONFIG_PATH=%q\n' "$config_path"
  } > "$STATE_FILE.tmp"
  mv "$STATE_FILE.tmp" "$STATE_FILE"
}

write_compose_env() {
  local image_id="$1" substrate_path="$2" config_path="$3"
  umask 077
  {
    printf 'BACKBOND_APPLIANCE_IMAGE=%s\n' "$image_id"
    printf 'BACKBOND_SUBSTRATE_PATH=%s\n' "$substrate_path"
    printf 'BACKBOND_CONFIG_PATH=%s\n' "$config_path"
  } > "$HERE/.env.tmp"
  mv "$HERE/.env.tmp" "$HERE/.env"
}

verify_start_state() {
  local expected_env actual_env
  [ -f "$STATE_FILE" ] || fail 009 'no immutable release state exists'
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  [ -n "${CURRENT_IMAGE_ID:-}" ] && [ -n "${SUBSTRATE_PATH:-}" ] && [ -n "${CONFIG_PATH:-}" ] \
    || fail 013 'immutable release state is incomplete'
  expected_env="$(printf 'BACKBOND_APPLIANCE_IMAGE=%s\nBACKBOND_SUBSTRATE_PATH=%s\nBACKBOND_CONFIG_PATH=%s' \
    "$CURRENT_IMAGE_ID" "$SUBSTRATE_PATH" "$CONFIG_PATH")"
  actual_env="$(cat "$HERE/.env")"
  [ "$actual_env" = "$expected_env" ] || fail 013 'generated .env differs from immutable release state'
  [ "${BACKBOND_APPLIANCE_IMAGE:-$CURRENT_IMAGE_ID}" = "$CURRENT_IMAGE_ID" ] \
    || fail 015 'image environment override differs from immutable release state'
  [ "${BACKBOND_SUBSTRATE_PATH:-$SUBSTRATE_PATH}" = "$SUBSTRATE_PATH" ] \
    || fail 015 'substrate environment override differs from immutable release state'
  [ "${BACKBOND_CONFIG_PATH:-$CONFIG_PATH}" = "$CONFIG_PATH" ] \
    || fail 015 'config environment override differs from immutable release state'
}

case "$ACTION" in
  verify)
    verify_bundle
    ;;
  install|upgrade)
    require_runtime
    verify_bundle
    new_id="$(load_image)"
    substrate_path="${BACKBOND_SUBSTRATE_PATH:-./synthetic-fixture}"
    config_path="${BACKBOND_CONFIG_PATH:-./config}"
    write_state "$new_id" "$substrate_path" "$config_path"
    write_compose_env "$new_id" "$substrate_path" "$config_path"
    printf 'PASS  sealed OCI image loaded\n'
    printf 'READY appliance can start\n'
    printf 'NEXT  docker compose up -d\n'
    ;;
  start)
    require_runtime
    [ -f "$HERE/.env" ] || fail 008 'run install before start'
    verify_bundle
    verify_start_state
    docker compose --env-file "$HERE/.env" -f "$HERE/compose.yaml" up -d
    printf 'READY appliance started; inspect with: docker compose ps\n'
    ;;
  stop)
    require_runtime
    docker compose --env-file "$HERE/.env" -f "$HERE/compose.yaml" stop
    printf 'STOPPED collection is stopped; retained state is preserved\n'
    ;;
  status)
    require_runtime
    docker compose --env-file "$HERE/.env" -f "$HERE/compose.yaml" ps
    ;;
  rollback)
    require_runtime
    [ -f "$STATE_FILE" ] || fail 009 'no release history exists'
    # shellcheck disable=SC1090
    source "$STATE_FILE"
    [ -n "${PREVIOUS_IMAGE_ID:-}" ] || fail 010 'no previous image is recorded'
    docker image inspect "$PREVIOUS_IMAGE_ID" >/dev/null 2>&1 \
      || fail 011 'previous image is not available locally'
    old_current="$CURRENT_IMAGE_ID"
    write_compose_env "$PREVIOUS_IMAGE_ID" "$SUBSTRATE_PATH" "$CONFIG_PATH"
    umask 077
    {
      printf 'CURRENT_IMAGE_ID=%q\n' "$PREVIOUS_IMAGE_ID"
      printf 'PREVIOUS_IMAGE_ID=%q\n' "$old_current"
      printf 'SUBSTRATE_PATH=%q\n' "$SUBSTRATE_PATH"
      printf 'CONFIG_PATH=%q\n' "$CONFIG_PATH"
    } > "$STATE_FILE.tmp"
    mv "$STATE_FILE.tmp" "$STATE_FILE"
    docker compose --env-file "$HERE/.env" -f "$HERE/compose.yaml" up -d --force-recreate
    printf 'PASS  rollback activated previous immutable image\n'
    ;;
  uninstall)
    require_runtime
    docker compose --env-file "$HERE/.env" -f "$HERE/compose.yaml" down
    printf 'STOPPED containers removed; named state volume is preserved\n'
    ;;
  *)
    fail 012 'usage: ./install.sh verify|install|upgrade|start|stop|status|rollback|uninstall'
    ;;
esac
