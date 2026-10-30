"""Offline seeded expansion of existing SYN cases; scenarios are not new cases."""
from dataclasses import asdict
import argparse
import hashlib
import json

from tools.assessment_test_catalog import SYNTHETIC_CASES, SYNTHETIC_DIALOGUE_CASES, SYNTHETIC_PRECISION_CASES, SYNTHETIC_EXPOSURE_CASES, SYNTHETIC_VERSIONS


def scenario_nonce(seed, case_id, repetition=0):
    """Stable task facts shared by generation and runtime execution."""
    if not isinstance(seed, str) or not seed:
        raise ValueError("seed must be a nonempty string")
    if type(repetition) is not int or repetition < 0:
        raise ValueError("repetition must be a nonnegative integer")
    identity = json.dumps(["synthetic-scenarios@1", seed, case_id, repetition], separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()[:32]


def generate_scenarios(*, seed: str, volume: int = 1):
    """Yield volume repetitions of every variant, preserving parent case identity."""
    if not isinstance(seed, str) or not seed:
        raise ValueError("seed must be a nonempty string")
    if type(volume) is not int or not 1 <= volume <= 10000:
        raise ValueError("volume must be an integer between 1 and 10000")
    from evals.oss_pilots import synthetic_assurance as legacy, synthetic_dialogue as dialogue, synthetic_precision as precision, synthetic_exposure as exposure
    for case_id in sorted(SYNTHETIC_CASES.values()):
        module = (exposure if case_id in SYNTHETIC_EXPOSURE_CASES.values() else
                  precision if case_id in SYNTHETIC_PRECISION_CASES.values() else
                  dialogue if case_id in SYNTHETIC_DIALOGUE_CASES.values() else legacy)
        for repetition in range(volume):
            for variant in module.VARIANTS[case_id]:
                identity = json.dumps(["synthetic-scenarios@1", seed, case_id, repetition, variant], separators=(",", ":"))
                nonce = scenario_nonce(seed, case_id, repetition)
                if module is not legacy:
                    initial, turns, _ = module.scenario(case_id, variant, nonce)
                else:
                    initial = legacy.setup(case_id, variant, nonce)
                    turns = (initial.user_prompt,)
                yield {"scenario_id": "SYN-SCENARIO-" + hashlib.sha256(identity.encode()).hexdigest(),
                       "case_id": case_id, "case_version": SYNTHETIC_VERSIONS[case_id],
                       "variant": variant, "repetition": repetition, "nonce": nonce,
                       "setup": asdict(initial), "turns": turns,
                       "acquisition_basis": "generated_scenario_only", "live_observation_count": 0,
                       "score_input": False, "requirement_conclusion": "not assessed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--volume", type=int, default=1, help="repetitions per case variant; generated scenarios are not observed tests")
    args = parser.parse_args()
    for row in generate_scenarios(seed=args.seed, volume=args.volume):
        print(json.dumps(row, sort_keys=True))


if __name__ == "__main__":
    main()
