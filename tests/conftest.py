"""Pytest configuration for the are-scanner test suite.

Sets ARE_ALLOW_DEV_SIGNING_KEY=true so existing tests that build/verify
forensic bundles can run without a production signing key.  Tests that
verify the key-absent behaviour explicitly patch this env var away.
"""
import os

os.environ.setdefault("ARE_ALLOW_DEV_SIGNING_KEY", "true")
