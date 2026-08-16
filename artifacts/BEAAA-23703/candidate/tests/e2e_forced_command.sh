#!/bin/bash
# End-to-end proof for the BEAAA-23703 candidate against a real OpenSSH server
# and a real scp/ssh/sftp client.
#
#   tests/e2e_forced_command.sh <path-to-d9-release.tgz>
#
# Build the archive exactly as the d9 deploy job does:
#
#   git worktree add --detach /tmp/d9wt d9d680f53db7de263f026be5284c97229126872c
#   cd /tmp/d9wt && tar -czf /tmp/real-agent-demo-release.tgz \
#     api harness rubric scanners release release_identity.py tools \
#     demo-real-agent tests/probe_sets \
#     artifacts/beaaa-152/datasets/halueval_dialogue_summ_general_n300.json \
#     artifacts/beaaa-18720/unified-probe-inventory.jsonl
#
# Isolation: the gate's own bytes are loaded unmodified, but a shim relocates
# the release root into a temporary directory, so nothing under
# /opt/backbond-real-agent-demo is created, read, or removed. The shim replaces
# os.execve with a recorder, so deploy.sh can never run from this harness. The
# machine that produced this candidate is the demo host itself, so both
# protections matter.
#
# The one-shot authorization is not exercised here: it lives in the root helper
# that deploy.sh invokes, which needs root and the protected env file. Its
# behaviour is covered by tests/test_d9_host_gate.py.
set -u

ARCHIVE="${1:?usage: e2e_forced_command.sh <release.tgz>}"
CANDIDATE="$(cd "$(dirname "$0")/.." && pwd)"
GATE="$CANDIDATE/backbond-real-agent-demo-ssh-gate"
SHA=d9d680f53db7de263f026be5284c97229126872c
A68=a68e7e0969b45e7eb8add18ff2007a305a0156aa
WORK="$(mktemp -d /tmp/d9-e2e-XXXXXX)"
RELEASES="$WORK/opt/backbond-real-agent-demo/releases"
RELEASE_DIR="$RELEASES/$SHA"
PORT=2222
PASS=0
FAIL=0

cleanup() {
  [ -f "$WORK/sshd.pid" ] && kill "$(cat "$WORK/sshd.pid")" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

check() { # check <name> <expected-rc> <actual-rc>
  if [ "$2" = "$3" ]; then
    PASS=$((PASS + 1)); printf 'PASS  %s (rc=%s)\n' "$1" "$3"
  else
    FAIL=$((FAIL + 1)); printf 'FAIL  %s (want rc=%s, got rc=%s)\n' "$1" "$2" "$3"
  fi
}

expect_output() { # expect_output <name> <needle> <haystack>
  case "$3" in
    *"$2"*) PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1" ;;
    *) FAIL=$((FAIL + 1)); printf 'FAIL  %s\n      got: %s\n' "$1" "$3" ;;
  esac
}

mkdir -p "$RELEASES"

cat > "$WORK/shim" <<EOF
#!/usr/bin/python3
import importlib.machinery, importlib.util, os, sys

loader = importlib.machinery.SourceFileLoader("gate", "$GATE")
spec = importlib.util.spec_from_loader("gate", loader)
gate = importlib.util.module_from_spec(spec)
loader.exec_module(gate)

gate.RELEASES = "$RELEASES"
gate.RELEASE_DIR = "$RELEASE_DIR"
gate.ARCHIVE_PATH = gate.RELEASE_DIR + "/" + gate.ARCHIVE_NAME
gate.DEPLOY_SCRIPT = gate.RELEASE_DIR + "/demo-real-agent/deploy.sh"
gate.MKDIR_SHAPE = "mkdir -p " + gate.RELEASE_DIR
gate.EXTRACT_SHAPE = "cd " + gate.RELEASE_DIR + " && tar -xzf " + gate.ARCHIVE_NAME
gate.SCP_SINK_SHAPES = frozenset({
    "scp -t " + gate.RELEASE_DIR + "/", "scp -t " + gate.RELEASE_DIR,
    "scp -t -- " + gate.RELEASE_DIR + "/", "scp -t -- " + gate.RELEASE_DIR,
})

def blocked(path, argv, env):
    print("WOULD_EXEC " + " ".join(argv), file=sys.stderr)
    print("WOULD_EXEC_ENV " + " ".join(sorted(env)), file=sys.stderr)
    raise SystemExit(97)

os.execve = blocked
gate.main()
EOF
chmod 755 "$WORK/shim"

# Any attempt to elevate would land here instead of the real binary.
mkdir -p "$WORK/bin"
cat > "$WORK/bin/sudo" <<EOF
#!/bin/sh
echo "\$@" >> "$WORK/sudo-was-called"
exit 1
EOF
chmod 755 "$WORK/bin/sudo"

ssh-keygen -q -t ed25519 -N '' -f "$WORK/host_ed25519"
ssh-keygen -q -t ed25519 -N '' -f "$WORK/client"
printf 'restrict,command="%s" %s\n' "$WORK/shim" "$(cat "$WORK/client.pub")" > "$WORK/authorized_keys"
chmod 600 "$WORK/authorized_keys" "$WORK/host_ed25519"
cat > "$WORK/sshd_config" <<EOF
Port $PORT
ListenAddress 127.0.0.1
HostKey $WORK/host_ed25519
AuthorizedKeysFile $WORK/authorized_keys
PasswordAuthentication no
UsePAM no
StrictModes no
PidFile $WORK/sshd.pid
Subsystem sftp /usr/lib/openssh/sftp-server
EOF

common=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
ssh_opts=(-i "$WORK/client" -p "$PORT" "${common[@]}")

PATH="$WORK/bin:$PATH" /usr/sbin/sshd -f "$WORK/sshd_config" -E "$WORK/sshd.log" || exit 1
sleep 1

live_before="$(docker ps --format '{{.ID}} {{.Names}} {{.Image}}' 2>/dev/null | sort)"

echo "--- the permitted d9 shapes"
ssh "${ssh_opts[@]}" 127.0.0.1 "mkdir -p $RELEASE_DIR" >/dev/null 2>&1
check "mkdir release directory" 0 $?
[ -d "$RELEASE_DIR" ] && { PASS=$((PASS + 1)); echo "PASS  release directory exists"; } \
                      || { FAIL=$((FAIL + 1)); echo "FAIL  release directory missing"; }

# The workflow runs plain scp, which on OpenSSH >= 9.0 speaks SFTP.
scp -i "$WORK/client" -P "$PORT" "${common[@]}" "$ARCHIVE" "127.0.0.1:$RELEASE_DIR/" >/dev/null 2>&1
check "upload over the workflow's scp (SFTP transport)" 0 $?
cmp -s "$ARCHIVE" "$RELEASE_DIR/real-agent-demo-release.tgz"
check "uploaded archive is byte-identical" 0 $?

rm -f "$RELEASE_DIR/real-agent-demo-release.tgz"
scp -O -i "$WORK/client" -P "$PORT" "${common[@]}" "$ARCHIVE" "127.0.0.1:$RELEASE_DIR/" >/dev/null 2>&1
check "upload over the legacy scp protocol" 0 $?
cmp -s "$ARCHIVE" "$RELEASE_DIR/real-agent-demo-release.tgz"
check "legacy-uploaded archive is byte-identical" 0 $?

extract_out="$(ssh "${ssh_opts[@]}" 127.0.0.1 \
  "cd $RELEASE_DIR && tar -xzf real-agent-demo-release.tgz" 2>&1)"
check "extract the release" 0 $?
expect_output "extract proves the pinned d9 tree" \
  "files=906 tree_sha256=5d6398a3cbf567778a8ca9ae7254b82a286a782dbd2874f0f711e9292bf16c17" \
  "$extract_out"

echo "--- the deploy leg stays unprivileged"
deploy_cmd="DEMO_GIT_SHA='$SHA' DEMO_DEPLOYED_AT='2026-08-16T15:04:05Z' bash '$RELEASE_DIR/demo-real-agent/deploy.sh' '$RELEASE_DIR'"
deploy_out="$(ssh "${ssh_opts[@]}" 127.0.0.1 "$deploy_cmd" 2>&1)"
check "deploy shape reaches execution" 97 $?
expect_output "release pin is proved before execution" \
  "release_pin=PASS sha=$SHA files=906" "$deploy_out"
expect_output "deploy runs unprivileged" "privilege=unprivileged" "$deploy_out"
expect_output "exec argv is bash deploy.sh <release>" \
  "WOULD_EXEC /bin/bash $RELEASE_DIR/demo-real-agent/deploy.sh $RELEASE_DIR" "$deploy_out"
expect_output "exec env carries only the six fixed names" \
  "WOULD_EXEC_ENV DEMO_DEPLOYED_AT DEMO_GIT_SHA HOME LOGNAME PATH USER" "$deploy_out"

echo "--- denials"
for command in \
  "mkdir -p $RELEASES/$A68" \
  "cd $RELEASES/$A68 && tar -xzf real-agent-demo-release.tgz" \
  "mkdir -p $RELEASE_DIR /tmp/extra" \
  "mkdir -p $RELEASE_DIR; whoami" \
  "cd $RELEASE_DIR && tar -xzf other.tgz" \
  "whoami" \
  "cat /etc/passwd" \
  "sudo -n /usr/local/sbin/backbond-real-agent-demo-secret-preflight" \
  "DEMO_GIT_SHA='$A68' DEMO_DEPLOYED_AT='2026-08-16T15:04:05Z' bash '$RELEASE_DIR/demo-real-agent/deploy.sh' '$RELEASE_DIR'"
do
  ssh "${ssh_opts[@]}" 127.0.0.1 "$command" >/dev/null 2>&1
  check "deny: $command" 1 $?
done

ssh "${ssh_opts[@]}" 127.0.0.1 >/dev/null 2>&1
check "deny: interactive shell" 1 $?

for target in "$WORK/escape.tgz" "/tmp/d9-e2e-escape.tgz"; do
  rm -f "$target"
  scp -i "$WORK/client" -P "$PORT" "${common[@]}" "$ARCHIVE" "127.0.0.1:$target" >/dev/null 2>&1
  [ -e "$target" ] && { FAIL=$((FAIL + 1)); echo "FAIL  SFTP escaped to $target"; } \
                   || { PASS=$((PASS + 1)); echo "PASS  deny: SFTP write to $target"; }
  scp -O -i "$WORK/client" -P "$PORT" "${common[@]}" "$ARCHIVE" "127.0.0.1:$target" >/dev/null 2>&1
  [ -e "$target" ] && { FAIL=$((FAIL + 1)); echo "FAIL  legacy scp escaped to $target"; } \
                   || { PASS=$((PASS + 1)); echo "PASS  deny: legacy scp write to $target"; }
done

printf 'put %s /tmp/d9-e2e-sftp-escape.tgz\n' "$ARCHIVE" > "$WORK/batch"
rm -f /tmp/d9-e2e-sftp-escape.tgz
sftp -b "$WORK/batch" -i "$WORK/client" -P "$PORT" "${common[@]}" 127.0.0.1 >/dev/null 2>&1
[ -e /tmp/d9-e2e-sftp-escape.tgz ] && { FAIL=$((FAIL + 1)); echo "FAIL  sftp put escaped"; } \
                                   || { PASS=$((PASS + 1)); echo "PASS  deny: sftp put outside the pinned path"; }

printf 'get %s/real-agent-demo-release.tgz %s/stolen.tgz\n' "$RELEASE_DIR" "$WORK" > "$WORK/batch-get"
sftp -b "$WORK/batch-get" -i "$WORK/client" -P "$PORT" "${common[@]}" 127.0.0.1 >/dev/null 2>&1
[ -e "$WORK/stolen.tgz" ] && { FAIL=$((FAIL + 1)); echo "FAIL  the sink allowed a read"; } \
                          || { PASS=$((PASS + 1)); echo "PASS  deny: the sink is write-only"; }

echo "--- tamper detection"
echo "backdoor" > "$RELEASE_DIR/api/backdoor.py"
tampered_out="$(ssh "${ssh_opts[@]}" 127.0.0.1 "$deploy_cmd" 2>&1)"
check "deny: tampered release" 1 $?
expect_output "tamper denial names the tree mismatch" "is not the pinned d9 tree" "$tampered_out"
rm -f "$RELEASE_DIR/api/backdoor.py"

echo "--- privilege and host state"
[ -e "$WORK/sudo-was-called" ] \
  && { FAIL=$((FAIL + 1)); echo "FAIL  the gate invoked sudo: $(cat "$WORK/sudo-was-called")"; } \
  || { PASS=$((PASS + 1)); echo "PASS  the gate never invoked sudo"; }
[ -e "/opt/backbond-real-agent-demo/releases/$SHA" ] \
  && { FAIL=$((FAIL + 1)); echo "FAIL  a d9 release directory was created on the host"; } \
  || { PASS=$((PASS + 1)); echo "PASS  no d9 release directory exists on the host"; }
[ -e "/etc/backbond/real-agent-demo-deploy.allow" ] \
  && { FAIL=$((FAIL + 1)); echo "FAIL  an arm file exists"; } \
  || { PASS=$((PASS + 1)); echo "PASS  nothing is armed"; }
if [ "$live_before" = "$(docker ps --format '{{.ID}} {{.Names}} {{.Image}}' 2>/dev/null | sort)" ]; then
  PASS=$((PASS + 1)); echo "PASS  running containers are unchanged"
else
  FAIL=$((FAIL + 1)); echo "FAIL  the set of running containers changed"
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
