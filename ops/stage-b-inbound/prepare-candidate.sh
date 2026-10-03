#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
candidate_commit=$(git -C "$repo_root" rev-parse HEAD)
printf '%s\n' "$candidate_commit" | grep -Eq '^[0-9a-f]{40}$' || {
    echo 'ERROR candidate_commit_missing_or_invalid' >&2
    exit 2
}
[ -z "$(git -C "$repo_root" status --short --untracked-files=no)" ] || {
    echo 'ERROR candidate_checkout_not_clean' >&2
    exit 2
}
printf '%s\n' "$candidate_commit" >"$repo_root/SOURCE_COMMIT"
test "$(cat "$repo_root/SOURCE_COMMIT")" = "$candidate_commit"
printf 'stage_b_candidate_ready=%s source_commit_file=%s\n' \
    "$candidate_commit" "$repo_root/SOURCE_COMMIT"
