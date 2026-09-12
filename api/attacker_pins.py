"""Pinned attacker models for multi-turn Garak probes (BEAAA-31910, BEAAA-31938).

Garak's multi-turn probes drive the subject with a second model — the attacker.
``probes/goat.py`` ships ``DEFAULT_PARAMS`` naming an unpinned HuggingFace
checkpoint (``Qwen/Qwen3-4B-Thinking-2507``, no ``revision``). That is two
problems at once: score drift caused by the attacker is unattributable, and the
scan host cannot hold ~8 GB of fp16 weights (15 GB RAM, CPU-only torch).

We pin the attacker to a dated API snapshot instead. The snapshot id is the pin;
there is no ``revision`` key to carry and nothing loads locally.

Determinism: this is an API attacker. temperature 0 plus a fixed seed is
provider best-effort, NOT a guarantee — BEAAA-31618 measured four distinct
response hashes across five calls at seed 0 / temperature 0. The minimum-sample
rule governs reproducibility here, not the seed. Do not describe this path with
the HuggingFace switch ``hf_args.do_sample: False``; that applies only when the
attacker runs locally, and it is not what is configured below.

atkgen.Tox (BEAAA-31938): the attacker is a local HuggingFace model
(``garak-llm/attackgeneration-toxicity_gpt2``). The checkpoint is small (~500 MB)
and already resident in the scan host's HF cache, so it runs locally rather than
via an API. The correct determinism switch for a local transformers pipeline is
``hf_args.do_sample: False`` (not ``temperature: 0``; transformers ignores
temperature when sampling is disabled). The pinned revision is the SHA currently
in the scan host's HF cache — pinning is a no-op behaviourally, closing only the
drift attribution hole.
"""

from __future__ import annotations

import os
from typing import Any

# OpenRouter serves the dated snapshots we hold keys for.
ATTACKER_BASE_URL = "https://openrouter.ai/api/v1"

# Probe-name -> attacker pin. Keyed by the garak probe module name.
#
# Two constraints found the hard way (BEAAA-31910), do not rediscover them:
#   * garak/generators/openai.py rejects any model name starting with "o" as an
#     o-class reasoning model, so OpenRouter's namespaced "openai/..." ids are
#     unusable with OpenAIGenerator.
#   * "thinking" snapshots return a turn whose text is None — garak does not read
#     the reasoning field — so they load cleanly and then produce nothing.
ATTACKER_PINS: dict[str, dict[str, Any]] = {
    "goat": {
        "red_team_model_type": "openai.OpenAIGenerator",
        "red_team_model_name": "qwen/qwen-plus-2025-07-28",
        "red_team_model_config": {
            "max_tokens": 512,
            "temperature": 0.0,
            "seed": 0,
        },
        "determinism": "temperature-0+seed, provider best-effort",
    },
    # BEAAA-31938: atkgen.Tox uses a local HF pipeline, not an API.
    # Pinning to the cached revision closes the drift hole without changing behaviour.
    "atkgen": {
        "red_team_model_type": "huggingface.Pipeline",
        "red_team_model_name": "garak-llm/attackgeneration-toxicity_gpt2",
        "red_team_model_config": {
            "hf_args": {
                "revision": "565f41dd443592c2146a604211468974d7a7d1a7",
                "do_sample": False,
            },
        },
        "determinism": "local pipeline, sampling disabled via hf_args, provider best-effort",
    },
}


def attacker_pin(probe_name: str) -> dict[str, Any] | None:
    """Return the pin for ``probe_name``, or None when the probe has no attacker."""
    return ATTACKER_PINS.get(probe_name.split(".")[0].strip().lower())


def attacker_pins_manifest_block() -> dict[str, dict[str, str]]:
    """The manifest's record of which attacker each multi-turn probe resolved to.

    Sits next to ``model_versions`` so a reader can attribute drift to the
    attacker as well as to the subject.
    """
    block: dict[str, dict[str, str]] = {}
    for probe, pin in ATTACKER_PINS.items():
        entry = {
            "attacker_name_resolved": pin["red_team_model_name"],
            "generator_class": pin["red_team_model_type"],
            "determinism": pin["determinism"],
        }
        revision = pin.get("red_team_model_config", {}).get("hf_args", {}).get("revision")
        if revision:
            # Local HuggingFace attackers carry the pin in the checkpoint
            # revision, not in the model name. Without this the manifest names
            # a moving branch and drift stays unattributable (BEAAA-31938).
            entry["attacker_revision"] = revision
        block[probe] = entry
    return block


def bind_attacker_credentials(env: dict[str, str] | None = None) -> dict[str, str]:
    """Bind the OpenAI-shaped env the generator reads to our OpenRouter key.

    ``OPENAI_API_KEY`` exists in the scan environment but is empty. Left alone,
    the generator authenticates with an empty string and the failure reads like
    a model problem. Only an empty/absent ``OPENAI_API_KEY`` is overwritten.
    """
    target = os.environ if env is None else env
    key = target.get("OPENROUTER_API_KEY", "")
    if key and not target.get("OPENAI_API_KEY"):
        target["OPENAI_API_KEY"] = key
    if not target.get("OPENAI_BASE_URL"):
        target["OPENAI_BASE_URL"] = ATTACKER_BASE_URL
    return dict(target) if env is not None else {}
