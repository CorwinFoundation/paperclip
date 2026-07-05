#!/usr/bin/env bash
# OSS clean-export pipeline (publish-prerequisite).
#
# Produces the public BackBond CI/CD-plugin repo as a SINGLE squashed commit
# under a neutral author identity, from a ref-scrubbed copy of the internal
# shipping set. Verified by an independent grep gate (0 internal refs) BEFORE
# any commit. This script DOES NOT PUBLISH — it emits a self-contained bundle
# for the separate, gated publish step.
#
# Usage:
#   tools/oss-export/clean_export.sh [<source-ref>] [<out-dir>]
#
#   <source-ref>  git ref/sha to export from (default: origin/master)
#   <out-dir>     output directory for the bundle (default: dist/oss-export)
#
# Reproducible: same <source-ref> + same tool version => byte-identical tree.
set -euo pipefail

SRC_REF="${1:-origin/master}"
OUT_DIR="${2:-dist/oss-export}"

# Resolve repo root from this script's location so the pipeline is runnable
# from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# The real shipping set. The Dockerfile COPYs the three api modules, so a bare
# ci_plugin/ export will not build — the api modules are load-bearing.
SHIPPING_PATHS=(
  ci_plugin
  api/__init__.py
  api/jcs.py
  api/ci_scan_ext.py
)

# Neutral author identity for the squashed public commit.
PUB_NAME="BackBond"
PUB_EMAIL="oss@backbond.ai"
PUB_MSG="BackBond Scanner CI/CD plugin (initial public release)"

SRC_SHA="$(git rev-parse "$SRC_REF")"
STAGE="$OUT_DIR/repo"
LOG_DIR="$OUT_DIR"

echo "== clean-export =="
echo "source-ref : $SRC_REF ($SRC_SHA)"
echo "out-dir    : $OUT_DIR"

rm -rf "$OUT_DIR"
mkdir -p "$STAGE"

# 1) Export the shipping set at the pinned ref into a clean tree (no history).
echo "-- exporting shipping set --"
git archive "$SRC_SHA" "${SHIPPING_PATHS[@]}" | tar -x -C "$STAGE"

# 2) Ref-scrub in place.
echo "-- scrubbing internal refs --"
python3 "$SCRIPT_DIR/scrub.py" scrub "$STAGE" | tee "$LOG_DIR/scrub.log"

# 3) Independent grep gate (second pass) BEFORE building the commit.
echo "-- grep gate (independent second pass) --"
python3 "$SCRIPT_DIR/scrub.py" gate "$STAGE" | tee "$LOG_DIR/gate.log"

# 4) Build the public repo: fresh git, single squashed commit, neutral author.
echo "-- building squashed public repo --"
(
  cd "$STAGE"
  rm -rf .git
  git init -q
  git checkout -q -b main
  git add -A
  GIT_AUTHOR_NAME="$PUB_NAME"   GIT_AUTHOR_EMAIL="$PUB_EMAIL" \
  GIT_COMMITTER_NAME="$PUB_NAME" GIT_COMMITTER_EMAIL="$PUB_EMAIL" \
    git commit -q -m "$PUB_MSG"
)

# 5) Emit a manifest recording provenance + verification for the publish step.
COMMIT_SHA="$(cd "$STAGE" && git rev-parse HEAD)"
COMMIT_COUNT="$(cd "$STAGE" && git rev-list --count HEAD)"
{
  echo "source_ref: $SRC_REF"
  echo "source_sha: $SRC_SHA"
  echo "public_commit_sha: $COMMIT_SHA"
  echo "public_commit_count: $COMMIT_COUNT"
  echo "public_author: $PUB_NAME <$PUB_EMAIL>"
  echo "gate: CLEAN (0 forbidden tokens)"
  echo "files:"
  (cd "$STAGE" && git ls-files | sed 's/^/  - /')
} > "$LOG_DIR/MANIFEST.txt"

# 6) Also emit a git bundle for handoff to the (separate) publish step.
(cd "$STAGE" && git bundle create "../oss-plugin.bundle" --all >/dev/null 2>&1)

echo "== done =="
echo "public repo : $STAGE (1 commit: $COMMIT_SHA)"
echo "manifest    : $LOG_DIR/MANIFEST.txt"
echo "bundle      : $OUT_DIR/oss-plugin.bundle"
echo "NOTE: this pipeline does NOT publish. Publish is a separate, gated step."
