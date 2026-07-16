"""Pytest configuration for the are-scanner test suite.

Sets ARE_ALLOW_DEV_SIGNING_KEY=true so existing tests that build/verify
forensic bundles can run without a production signing key.  Tests that
verify the key-absent behaviour explicitly patch this env var away.
"""
import os
import sys

assert sys.version_info >= (3, 11), "are-scanner tests require Python 3.11+"

os.environ.setdefault("ARE_ALLOW_DEV_SIGNING_KEY", "true")
