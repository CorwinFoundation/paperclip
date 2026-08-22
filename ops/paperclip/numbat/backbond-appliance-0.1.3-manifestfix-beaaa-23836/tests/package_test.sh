#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"

bash -n "$HERE/install.sh"
for code in 001 002 003 004 005 006 007 008 009 010 011 012 013 014 015; do
  grep -q "^[[:space:]]*$code) check=" "$HERE/install.sh"
done

grep -q "printf 'ERROR BB-APPL-%s" "$HERE/install.sh"
grep -q "printf 'SAFE  appliance state was not changed" "$HERE/install.sh"
grep -q "printf 'CHECK %s" "$HERE/install.sh"
grep -q "printf 'FIX   %s" "$HERE/install.sh"
grep -q "printf 'HELP  collect: appliance version, OCI digest, error code, and redacted logs" "$HERE/install.sh"
grep -q -- '--require-hashes' "$HERE/Dockerfile"
grep -q 'read_only: true' "$HERE/compose.yaml"
grep -q 'cap_drop:' "$HERE/compose.yaml"
grep -q 'no-new-privileges:true' "$HERE/compose.yaml"
grep -q 'pids_limit:' "$HERE/compose.yaml"
grep -q 'mem_limit:' "$HERE/compose.yaml"
grep -q 'cpus:' "$HERE/compose.yaml"
grep -q 'backbond-appliance-state' "$HERE/compose.yaml"
grep -q 'SPDX-2.3' "$HERE/release/sbom.spdx.json"

integrity_tmp="$(mktemp -d)"
trap 'rm -rf "$integrity_tmp"' EXIT
cp -a "$HERE/." "$integrity_tmp/package"
"$integrity_tmp/package/install.sh" verify >/dev/null

sed -i '0,/read_only: true/s//read_only: false/' "$integrity_tmp/package/compose.yaml"
if "$integrity_tmp/package/install.sh" verify >"$integrity_tmp/verify.stdout" 2>"$integrity_tmp/verify.stderr"; then
  echo 'tampered compose.yaml passed installer verification' >&2
  exit 1
fi
grep -q 'ERROR BB-APPL-004 .*compose.yaml' "$integrity_tmp/verify.stderr"

mkdir "$integrity_tmp/bin"
cat > "$integrity_tmp/bin/docker" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = version ] || { [ "${1:-}" = compose ] && [ "${2:-}" = version ]; }; then
  exit 0
fi
printf '%s\n' "$*" >> "${BB_APPL_DOCKER_MUTATION_LOG:?}"
exit 0
EOF
chmod +x "$integrity_tmp/bin/docker"

start_fixture="$integrity_tmp/start-package"
cp -a "$HERE/." "$start_fixture"
mkdir -p "$integrity_tmp/start-state"
image_id="$(cat "$HERE/release/IMAGE_ID")"
cat > "$integrity_tmp/start-state/releases.env" <<EOF
CURRENT_IMAGE_ID=$image_id
PREVIOUS_IMAGE_ID=
SUBSTRATE_PATH=./substrate
CONFIG_PATH=./config
EOF
cat > "$start_fixture/.env" <<EOF
BACKBOND_APPLIANCE_IMAGE=$image_id
BACKBOND_SUBSTRATE_PATH=./substrate
BACKBOND_CONFIG_PATH=./config
EOF

run_start() {
  local package="$1" state="$2" log="$3"
  PATH="$integrity_tmp/bin:$PATH" BB_APPL_DOCKER_MUTATION_LOG="$log" \
    BACKBOND_APPLIANCE_STATE_DIR="$state" "$package/install.sh" start
}

clean_log="$integrity_tmp/start-clean.log"
COMPOSE_FILE=/attacker/compose.yaml \
  run_start "$start_fixture" "$integrity_tmp/start-state" "$clean_log" >/dev/null
grep -Fqx "compose --env-file $start_fixture/.env -f $start_fixture/compose.yaml up -d" "$clean_log"
if grep -Fq '/attacker/compose.yaml' "$clean_log"; then
  echo 'inherited COMPOSE_FILE redirected start' >&2
  exit 1
fi

assert_start_rejects() {
  local name="$1" expected_code="$2" package="$integrity_tmp/$name-package"
  cp -a "$start_fixture" "$package"
  shift 2
  "$@" "$package"
  local log="$integrity_tmp/$name.log"
  if run_start "$package" "$integrity_tmp/start-state" "$log" \
      >"$integrity_tmp/$name.stdout" 2>"$integrity_tmp/$name.stderr"; then
    echo "$name mutation passed start verification" >&2
    exit 1
  fi
  grep -q "ERROR BB-APPL-$expected_code" "$integrity_tmp/$name.stderr"
  test ! -e "$log"
}

mutate_image() { sed -i 's/^BACKBOND_APPLIANCE_IMAGE=.*/BACKBOND_APPLIANCE_IMAGE=sha256:altered/' "$1/.env"; }
mutate_substrate() { sed -i 's#^BACKBOND_SUBSTRATE_PATH=.*#BACKBOND_SUBSTRATE_PATH=./altered#' "$1/.env"; }
mutate_config() { sed -i 's#^BACKBOND_CONFIG_PATH=.*#BACKBOND_CONFIG_PATH=./altered#' "$1/.env"; }
add_override() { printf 'services: {}\n' > "$1/compose.override.yaml"; }
modify_manifested() { printf '\n# altered\n' >> "$1/compose.yaml"; }
delete_manifested() { rm "$1/config.example.yaml"; }

assert_start_rejects image 013 mutate_image
assert_start_rejects substrate 013 mutate_substrate
assert_start_rejects config 013 mutate_config
assert_start_rejects override 014 add_override
assert_start_rejects modified 004 modify_manifested
assert_start_rejects deleted 004 delete_manifested

for action in install upgrade; do
  state_dir="$integrity_tmp/state-$action"
  mutation_log="$integrity_tmp/docker-$action.log"
  if PATH="$integrity_tmp/bin:$PATH" \
    BB_APPL_DOCKER_MUTATION_LOG="$mutation_log" \
    BACKBOND_APPLIANCE_STATE_DIR="$state_dir" \
    "$integrity_tmp/package/install.sh" "$action" \
    >"$integrity_tmp/$action.stdout" 2>"$integrity_tmp/$action.stderr"; then
    echo "tampered compose.yaml passed $action verification" >&2
    exit 1
  fi
  grep -q 'ERROR BB-APPL-004 .*compose.yaml' "$integrity_tmp/$action.stderr"
  test ! -e "$mutation_log"
  test ! -e "$state_dir"
  test ! -e "$integrity_tmp/package/.env"
done
if grep -nE 'pip install|python3 required' "$HERE/README.md" "$HERE/INSTALL.md" "$HERE/install.sh"; then
  echo 'vendor path contains host-Python installation' >&2
  exit 1
fi
if grep -RniE 'drop-in insurance|insured customer|claim[- ]time|first partner|agreed contract placeholder|instrument nothing|content never leaves' \
  "$HERE/appliance" "$HERE/README.md" "$HERE/INSTALL.md" "$HERE/backbond-appliance.service"; then
  echo 'blocked external claim remains in package' >&2
  exit 1
fi
python3 - <<'PY'
from appliance import __version__
assert __version__ == "0.1.3", __version__
PY
echo 'PACKAGE CONTRACT: GREEN'
