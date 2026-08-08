#!/bin/sh
set -eu

candidate=${1:?usage: push_authoritative_probe_registry_candidate.sh CANDIDATE_SHA}
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

head=$(git -C "$repo" rev-parse HEAD)
test "$candidate" = "$head" || {
  echo "candidate SHA does not match HEAD: $candidate != $head" >&2
  exit 1
}

"$repo/scripts/authoritative_probe_registry_integration_gate.sh" "$repo"
git -C "$repo" push origin "$candidate:refs/heads/master"
