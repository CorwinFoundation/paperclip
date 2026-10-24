#!/usr/bin/env bash
# Install the official CLI in this job's temporary directory without sudo.
set -euo pipefail
if command -v gh >/dev/null; then
  exit 0
fi
test "$(uname -s)" = Linux
test "$(uname -m)" = x86_64
version=2.103.2
archive=gh_${version}_linux_amd64.tar.gz
tool_dir=$(mktemp -d "$RUNNER_TEMP/backbond-gh.XXXXXX")
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  "https://github.com/cli/cli/releases/download/v${version}/${archive}" \
  --output "$tool_dir/$archive"
printf '%s  %s\n' \
  62ccf747115ddc61237efffdde66a3c8f70ba249433dc64f70801089b59c59c8 \
  "$tool_dir/$archive" | sha256sum --check --status
tar -xzf "$tool_dir/$archive" -C "$tool_dir" --strip-components=1
echo "$tool_dir/bin" >> "$GITHUB_PATH"
