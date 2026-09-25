#!/bin/sh
# Run the same command N times and persist each command's real exit status.
# Usage: run_status_batch.sh OUT_DIR COUNT COMMAND [ARG ...]
set -u

if [ "$#" -lt 3 ]; then
    echo "usage: $0 OUT_DIR COUNT COMMAND [ARG ...]" >&2
    exit 2
fi

out_dir=$1
count=$2
shift 2
mkdir -p "$out_dir"

i=1
batch_status=0
while [ "$i" -le "$count" ]; do
    set +e
    "$@" >"$out_dir/$i.out" 2>"$out_dir/$i.err"
    run_status=$?
    set -e
    printf '%s\n' "$run_status" >"$out_dir/$i.exit"
    if [ "$run_status" -ne 0 ]; then
        batch_status=1
    fi
    i=$((i + 1))
done

exit "$batch_status"
