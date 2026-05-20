#!/usr/bin/env bash
# Daily Founder digest wrapper (BEAAA-599).
#
# Sources Telegram secrets, runs the engine (BEAAA-598) to render a PDF, then
# runs deliver.py to post the PDF to Telegram. Exit code propagates from
# deliver.py — non-zero on terminal Telegram failure.
#
# Env contract:
#   Required (loaded from $FOUNDER_DIGEST_SECRETS or its default path):
#       TELEGRAM_BOT_TOKEN
#       TELEGRAM_FOUNDER_CHAT_ID
#   Required (auto-injected in Paperclip heartbeats):
#       PAPERCLIP_API_URL, PAPERCLIP_API_KEY, PAPERCLIP_COMPANY_ID
#   Optional:
#       FOUNDER_DIGEST_SECRETS    absolute path to telegram.env
#       FOUNDER_DIGEST_OUT_DIR    output dir (default: dist/founder-digest)
#       FOUNDER_DIGEST_AS_OF      ISO timestamp or "now" (default: now)
#
# Fails loud if any required env var is unset after sourcing — never silently
# sends to no-one.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Default secrets path is the company's project-secrets volume.
DEFAULT_SECRETS="/home/beai-agent/.paperclip/instances/default/projects/59f8876e-e729-4dda-98f9-1317c2b50492/8e61a25d-a945-4569-9f9c-4bce1cfef97b/_default/.secrets/telegram.env"
SECRETS_PATH="${FOUNDER_DIGEST_SECRETS:-$DEFAULT_SECRETS}"

if [[ -f "$SECRETS_PATH" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$SECRETS_PATH"; set +a
else
    echo "founder-digest: secrets file not found at $SECRETS_PATH" >&2
    echo "founder-digest: set FOUNDER_DIGEST_SECRETS or place telegram.env at the default path" >&2
    exit 2
fi

# Loud presence check — never deliver to nowhere, never run engine without API auth.
missing=()
for var in TELEGRAM_BOT_TOKEN TELEGRAM_FOUNDER_CHAT_ID PAPERCLIP_API_URL PAPERCLIP_API_KEY PAPERCLIP_COMPANY_ID; do
    if [[ -z "${!var:-}" ]]; then
        missing+=("$var")
    fi
done
if (( ${#missing[@]} > 0 )); then
    echo "founder-digest: missing required env vars: ${missing[*]}" >&2
    echo "founder-digest: refusing to run — fix env and retry" >&2
    exit 2
fi

OUT_DIR="${FOUNDER_DIGEST_OUT_DIR:-$REPO_ROOT/dist/founder-digest}"
AS_OF="${FOUNDER_DIGEST_AS_OF:-now}"
mkdir -p "$OUT_DIR"

DATE_STAMP="$(date -u +%Y-%m-%d)"
PDF_PATH="$OUT_DIR/$DATE_STAMP.pdf"

echo "founder-digest: rendering -> $PDF_PATH (as-of=$AS_OF)"
python3 "$SCRIPT_DIR/run.py" --as-of "$AS_OF" --out "$PDF_PATH"
echo "founder-digest: render ok ($(stat -c%s "$PDF_PATH") bytes)"

echo "founder-digest: delivering -> Telegram chat $TELEGRAM_FOUNDER_CHAT_ID"
exec python3 "$SCRIPT_DIR/deliver.py" --pdf "$PDF_PATH" \
    --caption "Daily Founder digest — $DATE_STAMP UTC"
