#!/usr/bin/env bash
# are-codex-oauth-guard.sh
# Self-healing guard for codex_local OAuth symlinks (BEAAA-7616, root cause BEAAA-7607/7612).
#
# WHY: codex_local agents authenticate via ChatGPT OAuth read from <CODEX_HOME>/auth.json,
# which MUST be a symlink to the shared auto-refreshing token store ~/.codex/auth.json.
# An adapter re-seed of an agent codex-home can silently drop that symlink; combined with
# adapterConfig.env.OPENAI_API_KEY="" codex then makes a bare unauthenticated call -> 401.
# This guard re-creates the symlink if missing/broken. It is intentionally DECOUPLED from
# the Scanner Engineer's own heartbeat (chicken-and-egg: a 401'd agent can't repair its own
# auth), so it runs from the beai-agent user crontab.
#
# Scope: heal only. It NEVER writes a real API key and NEVER touches adapterConfig. If the
# shared token store itself is gone it logs CRITICAL and exits non-zero without creating a
# dangling link.
#
# Owner: CTO (agent 88e68019). Idempotent; safe to run on any interval.
#
# 2026-09-26 (CEO, BEAAA-35988): homes are now DISCOVERED, not allowlisted. The hardcoded
# GUARDED_HOMES list held 1 of 21 codex_local seats, so 11 seats sat without the symlink and
# 401'd on every wake while this guard reported healthy. A guard that needs a manual edit per
# seat is a guard that silently stops covering the fleet. Pass --dry-run to audit without writing.
#
# 2026-09-26 (CTO, BEAAA-36011): added a POSITIVE LIVENESS RECEIPT. The log is deliberately
# silent on a healthy pass -- only heals and criticals are written -- so a dead cron entry and a
# healthy fleet produce byte-identical logs, and every check in this family so far could only
# ever detect a fault the guard itself noticed while running. Absence of complaint was being read
# as evidence the guard ran. Each successful completion now stamps GUARD_OK_STATE with the time
# and the counts, and --assert-fresh turns "did the guard actually run recently, over how many
# seats" into a checkable assertion for a preflight or an operator. Read-only mode: it never heals.
#
# 2026-09-26 (CTO, BEAAA-36012, ratifying the above): added a COVERAGE FLOOR. checked=0 catches
# only total collapse; a drop from 22 seats to 3 was still silent. The guard now persists the
# highest seat count it has ever covered and logs CRITICAL + exits 4 if a later run covers fewer.
# Seats legitimately decommissioned are accepted with --reset-coverage. Healing still runs first:
# a coverage regression never suppresses a heal. Cron interval tightened */30 -> */5 in the same
# change (the log shows an active re-seeder clobbering a link repeatedly, so the exposure window
# between clobber and heal is the thing that matters, and it was up to 29 minutes).

set -uo pipefail

TARGET="${GUARD_TARGET:-/home/beai-agent/.codex/auth.json}"
LOG="${GUARD_LOG:-/home/beai-agent/.local/state/are-codex-oauth-guard.log}"
COVERAGE_STATE="${GUARD_COVERAGE_STATE:-/home/beai-agent/.local/state/are-codex-oauth-guard.coverage}"
OK_STATE="${GUARD_OK_STATE:-/home/beai-agent/.local/state/are-codex-oauth-guard.ok}"
# Three cron intervals (*/5). Tolerates one missed run without crying wolf.
OK_MAX_AGE="${GUARD_OK_MAX_AGE:-900}"
AGENTS_ROOT="${GUARD_AGENTS_ROOT:-/home/beai-agent/.paperclip/instances/default/companies/59f8876e-e729-4dda-98f9-1317c2b50492/agents}"

DRY_RUN=0
RESET_COVERAGE=0
ASSERT_FRESH=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)          DRY_RUN=1 ;;
    --reset-coverage)   RESET_COVERAGE=1 ;;
    --assert-fresh)     ASSERT_FRESH=1 ;;
    --assert-fresh=*)   ASSERT_FRESH=1; OK_MAX_AGE="${arg#*=}" ;;
    *) echo "usage: $(basename "$0") [--dry-run] [--reset-coverage] [--assert-fresh[=SECONDS]]" >&2; exit 64 ;;
  esac
done

# Discover every seat codex-home under the company root. Replaces a hand-maintained allowlist
# that covered 1 of 21 seats. A seat whose adapter is not codex_local simply gets an unused
# symlink, which is harmless and correct if its adapter is ever switched.
GUARDED_HOMES=()
while IFS= read -r d; do
  [[ -n "$d" ]] && GUARDED_HOMES+=("$d")
done < <(find "$AGENTS_ROOT" -mindepth 2 -maxdepth 2 -type d -name codex-home 2>/dev/null | sort)

mkdir -p "$(dirname "$LOG")"
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "$(ts) [$1] $2" >>"$LOG"; }

# --assert-fresh: read-only liveness check. Answers "did a full healthy pass complete
# recently, and over how many seats", which no other mode can answer -- a silent log is
# equally consistent with a healthy fleet and a cron entry that was deleted. Heals nothing.
if [[ "$ASSERT_FRESH" == "1" ]]; then
  if ! [[ "$OK_MAX_AGE" =~ ^[0-9]+$ ]]; then
    echo "--assert-fresh: SECONDS must be a non-negative integer, got: $OK_MAX_AGE" >&2
    exit 64
  fi
  if [[ ! -r "$OK_STATE" ]]; then
    echo "STALE: no guard receipt at $OK_STATE — the guard has not completed a pass since this check was introduced, or it is not running" >&2
    log "CRITICAL" "--assert-fresh: receipt $OK_STATE absent; cannot show the guard ran"
    exit 5
  fi
  now=$(date -u +%s)
  stamped=$(date -u -r "$OK_STATE" +%s 2>/dev/null || echo 0)
  age=$((now - stamped))
  receipt=$(cat "$OK_STATE" 2>/dev/null | tr -d '\n')
  if [[ "$age" -gt "$OK_MAX_AGE" ]]; then
    echo "STALE: guard receipt is ${age}s old (max ${OK_MAX_AGE}s) — last pass: $receipt" >&2
    log "CRITICAL" "--assert-fresh: receipt ${age}s old exceeds ${OK_MAX_AGE}s; last pass: $receipt"
    exit 5
  fi
  echo "OK: guard pass ${age}s ago (max ${OK_MAX_AGE}s) — $receipt"
  exit 0
fi

healed=0
checked=0
destroyed=0
preserved=0

# BEAAA-36013 (revised after review by ca0edebc): classify a displaced regular file.
# Prints exactly one of: apikey | oauth | secret | other
#
# The first cut of this collapsed to secret/not-secret and matched token|refresh|bearer plus the
# JWT `ey...` shape. A valid OAuth auth.json is made of precisely those, so a per-seat `codex login`
# was classified identically to an injected API key and destroyed within 5 minutes. That fights the
# operator: this guard exists because a 401'd agent cannot repair its own auth, and per-seat login is
# what a human reaches for when the shared store breaks. OAuth-shaped files are now PRESERVED.
classify_displaced_file() {
  if ! command -v python3 >/dev/null 2>&1; then
    # No parser: cannot tell a login from an injection, and guessing wrong is destructive in one
    # direction and a credential spill in the other. Refuse to classify; caller leaves the file.
    echo "unclassifiable"; return 0
  fi
  python3 - "$1" <<'PY'
import json, re, sys

APIKEY_VALUE = re.compile(r'sk-[A-Za-z0-9_-]{16,}')
JWT_VALUE    = re.compile(r'ey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.')
SECRET_KEY   = re.compile(r'(api[_-]?key|secret|password|passwd|credential)', re.I)
TOKEN_FIELDS = ('access_token', 'refresh_token', 'id_token', 'account_id')

def nonempty(v):
    return isinstance(v, str) and v.strip() != ''

def any_value(node, pred, keyname=''):
    if isinstance(node, dict):
        return any(any_value(v, pred, k) for k, v in node.items())
    if isinstance(node, list):
        return any(any_value(v, pred, keyname) for v in node)
    return pred(node, keyname)

def classify(doc):
    # 1. The injection: a non-empty OPENAI_API_KEY, or any sk--shaped value anywhere.
    if isinstance(doc, dict) and nonempty(doc.get('OPENAI_API_KEY')):
        return 'apikey'
    if any_value(doc, lambda v, k: nonempty(v) and bool(APIKEY_VALUE.search(v))):
        return 'apikey'
    # 2. A per-seat OAuth login: secrets live under `tokens`, and OPENAI_API_KEY is absent or null.
    if isinstance(doc, dict):
        toks = doc.get('tokens')
        if isinstance(toks, dict) and any(nonempty(toks.get(f)) for f in TOKEN_FIELDS):
            return 'oauth'
        if doc.get('auth_mode') == 'chatgpt' and any_value(doc, lambda v, k: nonempty(v) and bool(JWT_VALUE.search(v))):
            return 'oauth'
    # 3. Some other credential we cannot attribute. Not the login shape, so still a spill.
    if any_value(doc, lambda v, k: nonempty(v) and bool(SECRET_KEY.search(k))):
        return 'secret'
    if any_value(doc, lambda v, k: nonempty(v) and bool(JWT_VALUE.search(v))):
        return 'secret'
    return 'other'

try:
    doc = json.load(open(sys.argv[1]))
except Exception:
    # Unparseable. Raw scan, biased toward destroying, per review: acceptable risk here. An OAuth
    # file written by `codex login` is always valid JSON, so this branch cannot eat a login.
    try:
        raw = open(sys.argv[1], 'rb').read(65536).decode('utf-8', 'replace')
    except Exception:
        print('unclassifiable'); sys.exit(0)
    print('secret' if (APIKEY_VALUE.search(raw) or JWT_VALUE.search(raw) or SECRET_KEY.search(raw)) else 'other')
    sys.exit(0)
print(classify(doc))
PY
}

# The shared OAuth store must exist before we can point anything at it.
if [[ ! -e "$TARGET" ]]; then
  log "CRITICAL" "shared OAuth store missing at $TARGET — cannot heal any link. Manual intervention required (re-auth ~/.codex)."
  exit 2
fi

for HOME_DIR in "${GUARDED_HOMES[@]}"; do
  LINK="$HOME_DIR/auth.json"
  checked=$((checked + 1))

  if [[ ! -d "$HOME_DIR" ]]; then
    log "WARN" "codex-home missing: $HOME_DIR (agent not seeded?) — skipping"
    continue
  fi

  # Healthy: symlink that points exactly at TARGET and resolves to a real file.
  # BEAAA-36013: healthy = a symlink that RESOLVES to TARGET, not one whose first hop is a string
  # match. Seeding points seats at <company>/codex-home/auth.json, itself a healthy link to TARGET;
  # codex authenticates fine through that chain. The old exact-string test rewrote those links every
  # interval, producing 45 cosmetic HEALED lines that buried the real absent-link outage.
  if [[ -L "$LINK" && "$(readlink -f "$LINK")" == "$TARGET" && -e "$LINK" ]]; then
    continue
  fi

  # Capture prior state for the audit trail.
  if [[ -L "$LINK" ]]; then
    prior="dangling/wrong-target symlink -> $(readlink "$LINK")"
  elif [[ -e "$LINK" ]]; then
    prior="non-symlink regular file present"
  else
    prior="absent"
  fi

  # Heal. ln -sfn replaces a broken link, wrong-target link, or absent path atomically.
  # If a real regular file is in the way, archive it first (never silently delete agent state).
  if [[ "$DRY_RUN" == "1" ]]; then
    healed=$((healed + 1))
    log "DRYRUN" "$LINK would be healed [$prior]"
    continue
  fi

  if [[ -e "$LINK" && ! -L "$LINK" ]]; then
    # BEAAA-36013: "archive whatever was in the way" and "the thing in the way may be a secret"
    # were never reconciled. Five guard-bak files held a live sk-proj key at 0600 from 2026-06-28
    # to 2026-09-26 in a path no inventory covers. Three outcomes now, not one:
    #   apikey -> fingerprint to the log, then DESTROY. The bytes disclosed no owner (/v1/models
    #             returns no openai-organization header) and existed nowhere else, so a second
    #             copy buys nothing and costs an unaudited plaintext credential.
    #   oauth  -> PRESERVE at 0600. Somebody's login, not an unmanaged spill, and the only copy
    #             of a session a human had to create by hand. WARN, not CRITICAL: no writer to hunt.
    #   other  -> archive as always, so ordinary displaced agent state is never silently deleted.
    # Healing proceeds in every classified case; a seat left on an injected key is the fault we
    # are removing.
    klass="$(classify_displaced_file "$LINK")"
    fp="$(sha256sum <"$LINK" | cut -d" " -f1)"
    bytes="$(wc -c <"$LINK")"
    mt="$(date -u -r "$LINK" +%Y-%m-%dT%H:%M:%SZ)"
    case "$klass" in
      apikey)
        log "CRITICAL" "API-key credential displaced at $LINK (sha256=$fp bytes=$bytes mtime=$mt). NOT archived: copying it would mint an unmanaged plaintext secret. Destroyed after fingerprinting. Something wrote an API key where an OAuth symlink belongs — find the writer, and rotate anything this fingerprint identifies."
        rm -f "$LINK" 2>>"$LOG"
        destroyed=$((destroyed + 1))
        ;;
      secret)
        log "CRITICAL" "unattributable credential-shaped file displaced at $LINK (sha256=$fp bytes=$bytes mtime=$mt), not the codex OAuth login shape. Destroyed after fingerprinting rather than copied to a sibling path. Identify it by fingerprint; rotate whatever it belongs to."
        rm -f "$LINK" 2>>"$LOG"
        destroyed=$((destroyed + 1))
        ;;
      oauth)
        bak="$LINK.guard-bak.$(date -u +%Y%m%dT%H%M%SZ)"
        if mv -f "$LINK" "$bak" 2>>"$LOG"; then
          chmod 600 "$bak" 2>>"$LOG"
          log "WARN" "per-seat OAuth login displaced at $LINK (sha256=$fp bytes=$bytes mtime=$mt); the shared store $TARGET is canonical. PRESERVED at $bak (0600) — this is a login somebody created by hand, not a spill, so it is not destroyed. Delete it once you have confirmed the shared store works for this seat."
          preserved=$((preserved + 1))
        else
          log "ERROR" "failed to preserve displaced OAuth login at $LINK — leaving it in place, not healing this seat"
          continue
        fi
        ;;
      other)
        mv -f "$LINK" "$LINK.guard-bak.$(date -u +%Y%m%dT%H%M%SZ)" 2>>"$LOG"
        ;;
      *)
        log "CRITICAL" "cannot classify displaced file at $LINK (sha256=$fp bytes=$bytes mtime=$mt) — no python3, or unreadable. Refusing to guess: destroying could eat a hand-made login, archiving could mint a plaintext secret copy. Left in place; THIS SEAT IS NOT HEALED until a human resolves it."
        continue
        ;;
    esac
  fi

  if ln -sfn "$TARGET" "$LINK" 2>>"$LOG" && [[ -e "$LINK" ]]; then
    healed=$((healed + 1))
    log "HEALED" "$LINK was [$prior] -> relinked to $TARGET"
  else
    log "ERROR" "failed to heal $LINK (prior: $prior)"
  fi
done

# Only emit a heartbeat line when something happened, to keep the log signal-dense.
if [[ "$healed" -gt 0 ]]; then
  log "SUMMARY" "checked=$checked healed=$healed credentials_destroyed=$destroyed logins_preserved=$preserved"
fi

if [[ "$checked" -eq 0 ]]; then
  log "CRITICAL" "discovered 0 codex-home dirs under $AGENTS_ROOT — guard is covering nothing"
  exit 3
fi

# COVERAGE FLOOR. Under-coverage is the bug class that caused BEAAA-35987/35988: the guard
# reported healthy while covering 1 of 21 seats. checked=0 catches only the total-collapse case.
# Compare against the high-water mark so a partial regression (22 -> 3) is loud too.
prev_max=0
if [[ -r "$COVERAGE_STATE" ]]; then
  read -r raw <"$COVERAGE_STATE" || raw=""
  [[ "$raw" =~ ^[0-9]+$ ]] && prev_max="$raw"
fi

if [[ "$RESET_COVERAGE" == "1" ]]; then
  log "INFO" "coverage high-water mark reset $prev_max -> $checked (operator-accepted seat count change)"
  prev_max=0
fi

if [[ "$DRY_RUN" != "1" ]]; then
  if [[ "$checked" -gt "$prev_max" ]]; then
    printf '%s\n' "$checked" >"$COVERAGE_STATE"
  elif [[ "$checked" -lt "$prev_max" ]]; then
    log "CRITICAL" "coverage regression: checked=$checked but this guard has previously covered $prev_max seats under $AGENTS_ROOT. Seats are unguarded, or the root moved. If seats were genuinely decommissioned, accept the new count with: $0 --reset-coverage"
    exit 4
  fi
else
  log "DRYRUN" "coverage checked=$checked high-water=$prev_max (state file not written)"
fi

# The receipt. Written only here, which is the single point every success path reaches
# after coverage has been validated -- so a stale receipt means "no pass completed
# cleanly", never "a pass completed but was unhappy". Dry runs never stamp it: auditing
# is not evidence the real thing ran.
if [[ "$DRY_RUN" != "1" ]]; then
  printf '%s checked=%s healed=%s credentials_destroyed=%s logins_preserved=%s\n' \
    "$(ts)" "$checked" "$healed" "$destroyed" "$preserved" >"$OK_STATE" 2>>"$LOG" \
    || log "ERROR" "failed to write liveness receipt $OK_STATE"
fi

exit 0
