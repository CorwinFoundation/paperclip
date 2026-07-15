#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${1:?usage: install_claude_judge_proxy.sh SOURCE_DIR}"
ENV_FILE="${REAL_AGENT_DEMO_ENV_FILE:-/etc/backbond/real-agent-demo.env}"
TEMPLATE="$SOURCE_DIR/demo-real-agent/systemd/backbond-claude-judge.service.in"
UNIT=/etc/systemd/system/backbond-claude-judge.service
SOCKET=/run/backbond-claude-judge/judge.sock
CLAUDE_BIN="$(command -v claude || true)"

if [[ $EUID -ne 0 ]]; then
  echo "Claude judge bridge installation requires root" >&2
  exit 1
fi
if [[ "$SOURCE_DIR" != /opt/backbond-real-agent-demo/* ]]; then
  echo "source directory must be under /opt/backbond-real-agent-demo" >&2
  exit 1
fi
if [[ -z "$CLAUDE_BIN" || ! -x "$CLAUDE_BIN" ]]; then
  echo "Claude Code is not installed" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" || ! -f "$TEMPLATE" ]]; then
  echo "judge environment or systemd template is missing" >&2
  exit 1
fi
if ! grep -Eq '^CLAUDE_JUDGE_PROXY_TOKEN=.{32,}$' "$ENV_FILE"; then
  echo "$ENV_FILE requires a 32+ character CLAUDE_JUDGE_PROXY_TOKEN" >&2
  exit 1
fi
if ! grep -Eq '^DEMO_JUDGE_BACKEND=claude-code-proxy$' "$ENV_FILE"; then
  echo "$ENV_FILE must select DEMO_JUDGE_BACKEND=claude-code-proxy" >&2
  exit 1
fi
if ! "$CLAUDE_BIN" auth status 2>/dev/null | python3 -c \
  'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("loggedIn") and d.get("authMethod")=="claude.ai" else 1)'; then
  echo "Claude Code must be logged in through claude.ai on this host" >&2
  exit 1
fi

if getent group 10001 >/dev/null; then
  SOCKET_GROUP="$(getent group 10001 | cut -d: -f1)"
elif getent group backbond-demo >/dev/null; then
  if [[ "$(getent group backbond-demo | cut -d: -f3)" != "10001" ]]; then
    echo "backbond-demo exists with the wrong gid" >&2
    exit 1
  fi
  SOCKET_GROUP=backbond-demo
else
  groupadd --gid 10001 backbond-demo
  SOCKET_GROUP=backbond-demo
fi

install -d -m 700 -o root -g root /var/lib/backbond-claude-judge
unit_tmp="$(mktemp /run/backbond-claude-judge.service.XXXXXX)"
trap 'rm -f "$unit_tmp"' EXIT
sed \
  -e "s|@SOURCE_DIR@|$SOURCE_DIR|g" \
  -e "s|@CLAUDE_BIN@|$CLAUDE_BIN|g" \
  -e "s|@SOCKET_GROUP@|$SOCKET_GROUP|g" \
  "$TEMPLATE" > "$unit_tmp"
install -m 644 -o root -g root "$unit_tmp" "$UNIT"
systemctl daemon-reload
systemctl enable --now backbond-claude-judge.service >/dev/null
systemctl restart backbond-claude-judge.service

for _ in $(seq 1 30); do
  if [[ -S "$SOCKET" ]]; then
    break
  fi
  sleep 1
done
if [[ ! -S "$SOCKET" ]]; then
  journalctl -u backbond-claude-judge.service -n 100 --no-pager >&2 || true
  echo "Claude judge socket did not become ready" >&2
  exit 1
fi

CLAUDE_JUDGE_PROXY_TOKEN="$(grep -E '^CLAUDE_JUDGE_PROXY_TOKEN=' "$ENV_FILE" | tail -n 1 | cut -d= -f2-)" \
  CLAUDE_JUDGE_MODEL="$(grep -E '^CLAUDE_JUDGE_MODEL=' "$ENV_FILE" | tail -n 1 | cut -d= -f2-)" \
  PYTHONPATH="$SOURCE_DIR" \
  CLAUDE_JUDGE_SOCKET="$SOCKET" \
  python3 "$SOURCE_DIR/demo-real-agent/claude_judge_runner.py" --health-check
echo "Claude Max judge bridge: ready"
