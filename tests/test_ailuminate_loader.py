"""Tests for harness.bench.ailuminate — BEAAA-218 sanity-load."""
from __future__ import annotations

import unittest

from harness.bench import ailuminate


class AILuminateLoaderTests(unittest.TestCase):
    def test_load_default_fixture(self) -> None:
        items = ailuminate.load_items()
        self.assertGreater(len(items), 1000, "DEMO set should have ~1290 rows")
        # All rows must carry the canonical AILuminate fields
        first = items[0]
        self.assertTrue(first.release_prompt_id.startswith("airr_practice_"))
        self.assertEqual(first.locale, "en_US")
        self.assertEqual(len(first.prompt_hash), 40)  # sha1-style hex

    def test_full_hazard_taxonomy_present(self) -> None:
        items = ailuminate.load_items()
        hist = ailuminate.hazard_histogram(items)
        # AILuminate v1.0 covers all 12 hazard categories — the DEMO
        # subset preserves the taxonomy. Confirm we see every category
        # so future re-pin to a new upstream release flags a regression.
        observed = set(hist.keys())
        self.assertEqual(
            observed,
            set(ailuminate.HAZARD_CATEGORIES),
            f"Hazard taxonomy drift — observed {observed} vs HAZARD_CATEGORIES",
        )

    def test_hazard_filter(self) -> None:
        items = ailuminate.load_items(hazard_filter={"hte"})
        self.assertGreater(len(items), 0)
        self.assertTrue(all(it.hazard == "hte" for it in items))

    def test_persona_filter(self) -> None:
        items = ailuminate.load_items(persona_filter={"skilled"})
        self.assertGreater(len(items), 0)
        self.assertTrue(all(it.persona == "skilled" for it in items))

    def test_limit(self) -> None:
        items = ailuminate.load_items(limit=10)
        self.assertEqual(len(items), 10)

    def test_header_validation_rejects_bad_schema(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write("id,prompt\nfoo,bar\n")
            bad_path = Path(fh.name)
        try:
            with self.assertRaises(ValueError) as ctx:
                ailuminate.load_items(bad_path)
            self.assertIn("AILuminate CSV header drift", str(ctx.exception))
        finally:
            bad_path.unlink()


if __name__ == "__main__":
    unittest.main()
