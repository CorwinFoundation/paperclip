#!/usr/bin/env bash
# unpushed-work-report.sh - report git work that exists only in a local working
# tree and has not been delivered to master.
#
# BEAAA-35892. An agent seat that commits without pushing strands that work: the
# commit is invisible to every other seat, and if the seat is paused it has no
# wake path to push it. This script makes that state enumerable so it is found
# by a report instead of by an incident.
#
# It is READ-ONLY by construction: it runs only git plumbing that reads
# (rev-parse, symbolic-ref, merge-base, ls-tree, ls-remote, log). It never
# pushes, fetches, merges, checks out, or deletes anything.
#
# Delivery is tested by PER-PATH BLOB COMPARISON, not by branch reachability.
# are-scanner squash-merges, so a delivered branch's head is normally
# unreachable from master afterwards; a reachability-only test reports delivered
# work as lost. For each path a branch changed:
#
#   master blob == branch-HEAD blob  -> that edit is on master            (landed)
#   path absent from both            -> deletion already on master        (landed)
#   master blob == branch-BASE blob  -> master never moved: definitely unlanded
#   otherwise                        -> master moved some other way       (ambiguous)
#
# "master" has to mean ONE master. A clone whose own origin/master is stale or
# points at a divergent remote makes every later edit look unlanded: measured
# against its own origin, workspace beaaa-25051-c1 reports 1,424 unlanded paths;
# measured against the real are-scanner master it reports 5. So the master blob
# map is taken from a reference repo (--reference-repo, default: the repo this
# script lives in) and applied to every working tree that shares its origin URL.
# Blob ids are content addresses, so a map built in one clone is directly
# comparable to another clone's trees. A tree whose origin URL matches no
# reference repo is measured against its own master and marked OWN-MASTER, which
# means "informational, not a delivery claim".
#
# A tree is reported UNLANDED only when at least one path is definitely
# unlanded. AMBIGUOUS means a later master edit touched the same paths and the
# owning issue has to say whether the branch still holds anything wanted.
#
# Usage:
#   scripts/unpushed-work-report.sh [--root DIR] [--unlanded-only] [--tsv]
#
#   --root DIR       directory whose children are searched for working trees.
#                    Repeatable. Default: $PAPERCLIP_WORKSPACES_ROOT, else
#                    ~/.paperclip/instances/default/workspaces.
#   --depth N        how deep below each root to look for working trees (default 2)
#   --unlanded-only  print only UNLANDED and AMBIGUOUS rows
#   --tsv            machine-readable output only (no summary)
#
# Output columns:
#   root_child  tree  branch  head  last_commit_date  verdict  n_unlanded  n_ambiguous

set -uo pipefail

ROOTS=()
REF_REPOS=()
DEPTH=2
UNLANDED_ONLY=0
TSV_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOTS+=("$2"); shift 2 ;;
    --reference-repo) REF_REPOS+=("$2"); shift 2 ;;
    --depth) DEPTH="$2"; shift 2 ;;
    --unlanded-only) UNLANDED_ONLY=1; shift ;;
    --tsv) TSV_ONLY=1; shift ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ ${#ROOTS[@]} -eq 0 ]; then
  ROOTS=("${PAPERCLIP_WORKSPACES_ROOT:-$HOME/.paperclip/instances/default/workspaces}")
fi

g() { git -C "$1" "${@:2}" 2>/dev/null; }

norm_url() { printf '%s' "$1" | sed -e 's#\.git$##' -e 's#/*$##' -e 's#^git@\([^:]*\):#https://\1/#'; }

# Cache of "<repo-common-dir>" -> file holding "<path>\t<blob>" for master.
CACHE_DIR="$(mktemp -d)"
trap 'rm -rf "$CACHE_DIR"' EXIT

MASTER_STALE=0

master_ref() {
  # Resolves the commit to compare against, and sets MASTER_STALE=1 when the
  # local clone has never fetched origin's current master. Staleness matters:
  # against an old master every later edit looks unlanded. Fixing it needs a
  # fetch, which this script will not do, so it reports the condition instead.
  local d="$1" ref remote
  MASTER_STALE=0
  remote="$(g "$d" ls-remote origin refs/heads/master refs/heads/main | awk 'NR==1{print $1}')"
  if [ -n "$remote" ]; then
    if g "$d" cat-file -e "$remote^{commit}"; then echo "$remote"; return 0; fi
    MASTER_STALE=1
  fi
  for ref in origin/master origin/main canon-master master main; do
    if g "$d" rev-parse --verify --quiet "$ref^{commit}" >/dev/null; then
      echo "$ref"; return 0
    fi
  done
  return 1
}

tree_blobs() {
  # $1 = worktree dir, $2 = rev. Prints path of a cached "<path>\t<blob>" file.
  local d="$1" rev="$2" common key cache sha
  common="$(g "$d" rev-parse --path-format=absolute --git-common-dir)"
  sha="$(g "$d" rev-parse "$rev")"
  key="$(printf '%s@%s' "$common" "$sha" | tr -c 'A-Za-z0-9' '_')"
  cache="$CACHE_DIR/$key"
  if [ ! -f "$cache" ]; then
    g "$d" ls-tree -r "$sha" | awk -F'\t' '{ split($1,a," "); print $2 "\t" a[3] }' > "$cache"
  fi
  echo "$cache"
}

if [ ${#REF_REPOS[@]} -eq 0 ]; then
  REF_REPOS=("$(cd "$(dirname "$0")/.." && pwd)")
fi

REF_URLS=(); REF_FILES=()
for repo in "${REF_REPOS[@]}"; do
  [ -e "$repo/.git" ] || continue
  rurl="$(norm_url "$(g "$repo" remote get-url origin)")"
  [ -n "$rurl" ] || continue
  rref=""
  for cand in origin/master origin/main master main; do
    if g "$repo" rev-parse --verify --quiet "$cand^{commit}" >/dev/null; then rref="$cand"; break; fi
  done
  [ -n "$rref" ] || continue
  REF_URLS+=("$rurl")
  REF_FILES+=("$(tree_blobs "$repo" "$rref")")
  [ "$TSV_ONLY" -eq 1 ] || echo "reference: $rurl at $(g "$repo" rev-parse --short "$rref") ($repo)" >&2
done

ref_map_for() {
  local url i
  url="$(norm_url "$(g "$1" remote get-url origin)")"
  [ -n "$url" ] || return 1
  for i in "${!REF_URLS[@]}"; do
    if [ "${REF_URLS[$i]}" = "$url" ]; then echo "${REF_FILES[$i]}"; return 0; fi
  done
  return 1
}

total=0; unlanded_trees=0; ambiguous_trees=0; landed_trees=0; pushed_trees=0; stale_trees=0; own_trees=0

report_tree() {
  local d="$1" label="$2" child="$3"
  local branch head ref base date verdict n_un=0 n_amb=0 remote_heads f hb bb mb

  head="$(g "$d" rev-parse HEAD)" || return 0
  [ -n "$head" ] || return 0
  branch="$(g "$d" symbolic-ref --quiet --short HEAD)" || branch=""
  [ -n "$branch" ] || branch="DETACHED"
  date="$(g "$d" log -1 --format=%cI HEAD)"; date="${date:0:10}"

  total=$((total + 1))

  if [ "$branch" != "DETACHED" ]; then
    remote_heads="$(g "$d" ls-remote --heads origin "refs/heads/$branch")"
    if [ -n "$remote_heads" ]; then
      pushed_trees=$((pushed_trees + 1))
      [ "$UNLANDED_ONLY" -eq 1 ] || printf '%s\t%s\t%s\t%s\t%s\t%s\t0\t0\n' \
        "$child" "$label" "$branch" "${head:0:10}" "$date" "PUSHED"
      return 0
    fi
  fi

  ref="$(master_ref "$d")" || {
    printf '%s\t%s\t%s\t%s\t%s\t%s\t0\t0\n' "$child" "$label" "$branch" "${head:0:10}" "$date" "NO-MASTER-REF"
    return 0
  }
  base="$(g "$d" merge-base HEAD "$ref")"
  if [ -z "$base" ]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\t0\t0\n' "$child" "$label" "$branch" "${head:0:10}" "$date" "NO-BASE"
    return 0
  fi
  if [ "$base" = "$head" ]; then
    landed_trees=$((landed_trees + 1))
    [ "$UNLANDED_ONLY" -eq 1 ] || printf '%s\t%s\t%s\t%s\t%s\t%s\t0\t0\n' \
      "$child" "$label" "$branch" "${head:0:10}" "$date" "ANCESTOR-OF-MASTER"
    return 0
  fi

  local mfile hfile bfile counts own_master=0
  if mfile="$(ref_map_for "$d")"; then :; else
    mfile="$(tree_blobs "$d" "$ref")"; own_master=1
  fi
  hfile="$(tree_blobs "$d" "$head")"
  bfile="$(tree_blobs "$d" "$base")"
  counts="$(g "$d" diff --name-only "$base" "$head" \
    | awk -F'\t' -v m="$mfile" -v h="$hfile" -v b="$bfile" '
        BEGIN {
          while ((getline line < m) > 0) { i=index(line,"\t"); M[substr(line,1,i-1)]=substr(line,i+1) }
          while ((getline line < h) > 0) { i=index(line,"\t"); H[substr(line,1,i-1)]=substr(line,i+1) }
          while ((getline line < b) > 0) { i=index(line,"\t"); B[substr(line,1,i-1)]=substr(line,i+1) }
        }
        $0 == "" { next }
        {
          p=$0
          hb = (p in H) ? H[p] : ""
          mb = (p in M) ? M[p] : ""
          if (hb != "" && hb == mb) next          # branch edit is on master
          if (hb == "" && mb == "") next          # deletion already on master
          bb = (p in B) ? B[p] : ""
          if (mb == bb) un++; else amb++          # master unmoved -> definitely unlanded
        }
        END { print un+0 "\t" amb+0 }')"
  n_un="${counts%%	*}"
  n_amb="${counts##*	}"

  if [ "$own_master" -eq 1 ] && [ "$n_un" -gt 0 ]; then
    verdict="OWN-MASTER"; own_trees=$((own_trees + 1))
  elif [ "$MASTER_STALE" -eq 1 ]; then
    verdict="STALE-MASTER-REF"; stale_trees=$((stale_trees + 1))
  elif [ "$n_un" -gt 0 ]; then
    verdict="UNLANDED"; unlanded_trees=$((unlanded_trees + 1))
  elif [ "$n_amb" -gt 0 ]; then
    verdict="AMBIGUOUS"; ambiguous_trees=$((ambiguous_trees + 1))
  else
    verdict="LANDED-CONTENT"; landed_trees=$((landed_trees + 1))
  fi

  if [ "$UNLANDED_ONLY" -eq 0 ] || [ "$verdict" != "LANDED-CONTENT" ]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$child" "$label" "$branch" "${head:0:10}" "$date" "$verdict" "$n_un" "$n_amb"
  fi
}

walk() {
  # $1 = directory, $2 = remaining depth, $3 = root-child label
  local d="$1" left="$2" child="$3" entry
  if [ -e "$d/.git" ]; then
    report_tree "$d" "$(basename "$d")" "$child"
    return 0
  fi
  [ "$left" -gt 0 ] || return 0
  for entry in "$d"/*; do
    [ -d "$entry" ] || continue
    walk "$entry" "$((left - 1))" "$child"
  done
}

for root in "${ROOTS[@]}"; do
  [ -d "$root" ] || { echo "no such root: $root" >&2; continue; }
  for child in "$root"/*; do
    [ -d "$child" ] || continue
    walk "$child" "$DEPTH" "$(basename "$child")"
  done
done

if [ "$TSV_ONLY" -eq 0 ]; then
  {
    echo
    echo "working trees examined: $total"
    echo "  pushed (branch on origin):        $pushed_trees"
    echo "  landed (content already on master): $landed_trees"
    echo "  AMBIGUOUS (master moved on same paths): $ambiguous_trees"
    echo "  UNLANDED (definitely not on master):    $unlanded_trees"
    echo "  OWN-MASTER (no reference repo for that origin; not a delivery claim): $own_trees"
    echo "  STALE-MASTER-REF (clone never fetched current master; not measurable): $stale_trees"
    echo
    echo "UNLANDED means the commits exist only in that working tree. Push them"
    echo "through the owning issue, or record them as deliberately abandoned."
  } >&2
fi

exit 0
