#!/usr/bin/env bash
# Use the version-matched browser already specified by the test's npm package.
# Do not install a global Chrome executable that would change other jobs' skips.
set -euo pipefail
npx --no-install playwright install --no-shell chromium
browser_path=$(node -e 'process.stdout.write(require("playwright").chromium.executablePath())')
test -x "$browser_path"
echo "BACKBOND_TEST_CHROME=$browser_path" >> "$GITHUB_ENV"
