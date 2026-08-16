#!/bin/sh
set -eu

test "$(id -u)" -eq 0
candidate_dir=${1:?candidate directory required}
target=/usr/local/sbin/backbond-real-agent-demo-secret-preflight
replacement=20c232da5c3e2cb87ac55fce3e8e576f722cf21e28746ce3801359d5fdc9a6e6
baseline=9ecc60538c9904a2e9c1583c7af2ffed9d37f9775fa3888b02313237a53874e2

test "$(sha256sum "$target" | awk '{print $1}')" = "$replacement"
test "$(sha256sum "$candidate_dir/backbond-real-agent-demo-secret-preflight.baseline-9ecc6053" | awk '{print $1}')" = "$baseline"

stage=$(mktemp /usr/local/sbin/.backbond-preflight.beaaa23772-rollback.XXXXXX)
trap 'rm -f "$stage"' EXIT HUP INT TERM
install -o root -g root -m 0755 "$candidate_dir/backbond-real-agent-demo-secret-preflight.baseline-9ecc6053" "$stage"
test "$(sha256sum "$stage" | awk '{print $1}')" = "$baseline"
mv -f "$stage" "$target"
trap - EXIT HUP INT TERM
