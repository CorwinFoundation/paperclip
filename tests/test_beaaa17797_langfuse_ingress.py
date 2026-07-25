from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "infra"
    / "langfuse-synthetic"
    / "ingress_gateway.py"
)
SPEC = importlib.util.spec_from_file_location("synthetic_ingress_gateway", MODULE_PATH)
assert SPEC and SPEC.loader
GATEWAY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATEWAY)


@pytest.mark.parametrize(
    "key", ["email", "phone", "customer_id", "customer_name", "pii"]
)
def test_prohibited_keys_are_found_at_any_depth_and_case(key: str) -> None:
    document = {"batch": [{"body": {"metadata": {key.upper(): "prohibited"}}}]}
    assert GATEWAY.prohibited_paths(document) == [
        f"$.batch[0].body.metadata.{key.upper()}"
    ]


def test_synthetic_fixture_shape_has_no_prohibited_keys() -> None:
    document = {
        "batch": [
            {
                "body": {
                    "metadata": {
                        "data_class": "synthetic-only",
                        "customer_data": False,
                        "authoritative_evidence": False,
                    }
                }
            }
        ]
    }
    assert GATEWAY.prohibited_paths(document) == []
