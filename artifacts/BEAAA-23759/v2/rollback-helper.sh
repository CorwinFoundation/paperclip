#!/bin/sh
set -eu

test "$(id -u)" -eq 0
candidate_dir=${1:?candidate directory required}
target=/usr/local/sbin/backbond-real-agent-demo-secret-preflight
replacement=9ecc60538c9904a2e9c1583c7af2ffed9d37f9775fa3888b02313237a53874e2
baseline=927fca8b796e7e868a319ab2fffb4372e34bb29228723090f31f6b994a46f6f8

test "$(sha256sum "$target" | awk '{print $1}')" = "$replacement"
test "$(sha256sum "$candidate_dir/backbond-real-agent-demo-secret-preflight.baseline-927fca8b" | awk '{print $1}')" = "$baseline"

stage=$(mktemp /usr/local/sbin/.backbond-preflight.rollback.XXXXXX)
trap 'rm -f "$stage"' EXIT HUP INT TERM
install -o root -g root -m 0755 "$candidate_dir/backbond-real-agent-demo-secret-preflight.baseline-927fca8b" "$stage"
test "$(sha256sum "$stage" | awk '{print $1}')" = "$baseline"
mv -f "$stage" "$target"
trap - EXIT HUP INT TERM
