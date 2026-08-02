#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
module_dir="$repo_root/tools/post-bind-ingest/otel-collector/build/backbond-otelcol"
output_path="$module_dir/backbond-otelcol"

cd "$module_dir"

expected_go_version=go1.25.0
actual_go_version=$(GOTOOLCHAIN=auto go env GOVERSION)
if [ "$actual_go_version" != "$expected_go_version" ]; then
  echo "Go toolchain mismatch: expected $expected_go_version, got $actual_go_version" >&2
  exit 1
fi

CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOTOOLCHAIN=auto \
  go build -trimpath -buildvcs=false -ldflags='-buildid=' -o "$output_path" .

sha256sum "$output_path"
