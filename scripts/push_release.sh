#!/usr/bin/env bash
# Explicit guarded push path for release branches blocked by .githooks/post-commit.
# BEAAA-5283 / BEAAA-5266-F05; rule-execution; are-scanner-api-v1@v1.0.

set -euo pipefail

branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
if [[ -z "$branch" ]]; then
  echo "[release-push] detached HEAD; refusing push" >&2
  exit 2
fi

case "$branch" in
  master|main|release/*) ;;
  *)
    description="$(git config --get "branch.${branch}.description" 2>/dev/null || true)"
    protected_flag="$(git config --bool --get "branch.${branch}.protected" 2>/dev/null || true)"
    if ! printf '%s\n' "$description" | grep -qE '(^|[[:space:]])protected:' \
        && [[ "$protected_flag" != "true" ]]; then
      echo "[release-push] '$branch' is not a guarded release/protected branch; use git push directly" >&2
      exit 2
    fi
    ;;
esac

case "${BACKBOND_RELEASE_PUSH_APPROVER_ROLE:-}" in
  CEO|CTO) ;;
  *)
    echo "[release-push] set BACKBOND_RELEASE_PUSH_APPROVER_ROLE=CEO or CTO" >&2
    exit 2
    ;;
esac

if [[ ! "${BACKBOND_RELEASE_PUSH_REF:-}" =~ ^BEAAA-[0-9]+$ ]]; then
  echo "[release-push] set BACKBOND_RELEASE_PUSH_REF=BEAAA-NNN for audit traceability" >&2
  exit 2
fi

push_cmd=(git)
if [[ -n "${GITHUB_PAT_ARE_SCANNER:-}" ]]; then
  push_cmd+=(-c "credential.helper=!f() { echo username=x-access-token; echo password=\$GITHUB_PAT_ARE_SCANNER; }; f")
fi
push_cmd+=(push --set-upstream origin "$branch")

echo "[release-push] pushing '$branch' with ${BACKBOND_RELEASE_PUSH_APPROVER_ROLE} approval ${BACKBOND_RELEASE_PUSH_REF}" >&2
"${push_cmd[@]}"
