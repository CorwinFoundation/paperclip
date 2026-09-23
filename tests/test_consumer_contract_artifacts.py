"""The landed consumer contracts must stay byte-identical to their artifacts.

BEAAA-34391 and BEAAA-34392 closed with these files attached to Paperclip and
never written to the repository, so the Stage B pilot restated them by hand
and the two drifted apart unnoticed. These digests are the bytes each
attachment served. Editing a landed file without re-cutting a new version
fails here instead of drifting quietly.

The files are versioned in their names. They are meant to be superseded by a
new file and a new digest, not edited in place.
"""

from __future__ import annotations

import hashlib
import json
import unittest

from stage_b_pilot import program

# sha256 of the bytes served by each Paperclip attachment.
EXPECTED_DIGESTS = {
    # BEAAA-34391 attachment e3d09bd1-9884-44a9-9257-7f3542e40ff7, 23132 bytes
    program.REQUIREMENTS_FILENAME: (
        "037582ca99000c48d94b14806a1b01330887eef60a81743fc1babbca783357dd"
    ),
    # BEAAA-34392 attachment 517c239d-ae9e-4b88-b5b0-ed799899aa35, 17792 bytes
    program.FIXTURES_FILENAME: (
        "ce6dfb3b588b021b962eb3c8a8ac5fe96876dd8ef66a265fe744c5154072e1ca"
    ),
    # BEAAA-34392 attachment 289c5505-1b66-401d-831d-1d157e56bea7, 9505 bytes
    program.PACKET_CONTRACT_FILENAME: (
        "92b96eed977c2f7b1cf13829ebe38584b87a91586646a170aa308e9bb2c94d4c"
    ),
}


class LandedContractDigestTests(unittest.TestCase):
    def test_every_landed_file_matches_its_attachment_digest(self) -> None:
        for filename, expected in EXPECTED_DIGESTS.items():
            with self.subTest(filename=filename):
                path = program.CONTRACTS_DIR / filename
                self.assertTrue(path.is_file(), f"{filename} is not landed")
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(actual, expected)

    def test_program_reads_the_artifact_rather_than_restating_it(self) -> None:
        raw = json.loads(
            (program.CONTRACTS_DIR / program.REQUIREMENTS_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        config = raw["stage_a_mock_configuration"]
        self.assertEqual(
            program.LIMITS.per_event_cents,
            config["illustrative_maximum_eligible_exposure_per_task_minor"],
        )
        self.assertEqual(
            program.LIMITS.program_period_cents,
            config["illustrative_program_period_limit_minor"],
        )
        self.assertEqual(program.LIMITS.currency, config["currency"])
        self.assertEqual(
            program.PROGRAM_REQUIREMENTS_VERSION, raw["schema_version"]
        )
        self.assertEqual(
            list(program.COVERED_WORKFLOWS), config["covered_workflows"]
        )

    def test_no_limit_figure_is_invented(self) -> None:
        """Every cents figure the pilot publishes traces to the artifact."""
        stated = set(
            json.loads(
                (program.CONTRACTS_DIR / program.REQUIREMENTS_FILENAME).read_text(
                    encoding="utf-8"
                )
            )["stage_a_mock_configuration"][key]
            for key in (
                "illustrative_maximum_eligible_exposure_per_task_minor",
                "illustrative_program_period_limit_minor",
            )
        )
        published = {
            value
            for key, value in program.as_dict()["limits"].items()
            if key.endswith("_cents")
        }
        self.assertTrue(published <= stated, f"unsourced limits: {published - stated}")


if __name__ == "__main__":
    unittest.main()
