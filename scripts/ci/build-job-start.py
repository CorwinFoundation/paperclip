#!/usr/bin/env python3
"""Image-owned hook. Called by Runner.Worker before any workflow steps."""
import json
import os
from build_pool_policy import check_environment

with open("/run/backbond/expected.json", encoding="utf-8") as source:
    check_environment(json.load(source), os.environ)
print("Admitted one job to its disposable build environment.")
