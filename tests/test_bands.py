"""12-band cut tests — BEAAA-57 v1.0 FROZEN 2026-04-30.

Boundary table (each crossing is a v1.0 freeze commitment):

    letter:  85↔84  70↔69  55↔54
    modifier: A+/A 95↔94, A/A- 90↔89, A-/B+ 85↔84, B+/B 80↔79, B/B- 75↔74,
              B-/C+ 70↔69, C+/C 65↔64, C/C- 60↔59, C-/D+ 55↔54,
              D+/D 40↔39, D/D- 20↔19
"""
from __future__ import annotations

import unittest

from rubric.bands import Band, BandError, assign_band, band_to_dict


class LetterCutTests(unittest.TestCase):
    def test_a_range(self):
        for bmi in (85, 90, 95, 100):
            self.assertEqual(assign_band(bmi).letter, "A")

    def test_b_range(self):
        for bmi in (70, 75, 79, 84):
            self.assertEqual(assign_band(bmi).letter, "B")

    def test_c_range(self):
        for bmi in (55, 60, 64, 69):
            self.assertEqual(assign_band(bmi).letter, "C")

    def test_d_range(self):
        for bmi in (0, 19, 39, 54):
            self.assertEqual(assign_band(bmi).letter, "D")

    def test_letter_boundary_84_b_plus_85_a_minus(self):
        self.assertEqual(assign_band(84).letter, "B")
        self.assertEqual(assign_band(85).letter, "A")

    def test_letter_boundary_69_c_plus_70_b_minus(self):
        self.assertEqual(assign_band(69).letter, "C")
        self.assertEqual(assign_band(70).letter, "B")

    def test_letter_boundary_54_d_plus_55_c_minus(self):
        self.assertEqual(assign_band(54).letter, "D")
        self.assertEqual(assign_band(55).letter, "C")


class ModifierCutTests(unittest.TestCase):
    cases = [
        # (bmi, letter, modifier, factor)
        (100, "A", "+", 0.93),
        (95, "A", "+", 0.93),
        (94, "A", "neutral", 1.00),
        (90, "A", "neutral", 1.00),
        (89, "A", "-", 1.07),
        (85, "A", "-", 1.07),
        (84, "B", "+", 0.93),
        (80, "B", "+", 0.93),
        (79, "B", "neutral", 1.00),
        (75, "B", "neutral", 1.00),
        (74, "B", "-", 1.07),
        (70, "B", "-", 1.07),
        (69, "C", "+", 0.93),
        (65, "C", "+", 0.93),
        (64, "C", "neutral", 1.00),
        (60, "C", "neutral", 1.00),
        (59, "C", "-", 1.07),
        (55, "C", "-", 1.07),
        (54, "D", "+", 0.93),
        (40, "D", "+", 0.93),
        (39, "D", "neutral", 1.00),
        (20, "D", "neutral", 1.00),
        (19, "D", "-", 1.10),
        (0, "D", "-", 1.10),
    ]

    def test_modifier_assignments(self):
        for bmi, letter, modifier, factor in self.cases:
            with self.subTest(bmi=bmi):
                b = assign_band(bmi)
                self.assertEqual(b.letter, letter)
                self.assertEqual(b.modifier, modifier)
                self.assertAlmostEqual(b.modifier_factor, factor, places=4)


class DisplayAndPostureTests(unittest.TestCase):
    def test_neutral_display_is_letter_only(self):
        self.assertEqual(assign_band(75).display(), "B")

    def test_plus_minus_display(self):
        self.assertEqual(assign_band(95).display(), "A+")
        self.assertEqual(assign_band(55).display(), "C-")

    def test_postures(self):
        self.assertEqual(assign_band(95).posture, "Preferred")
        self.assertEqual(assign_band(75).posture, "Standard")
        self.assertEqual(assign_band(60).posture, "Surplus (remediation endorsement)")
        self.assertEqual(assign_band(10).posture, "Decline / layered-excess only")


class GuardTests(unittest.TestCase):
    def test_non_int_raises(self):
        with self.assertRaises(BandError):
            assign_band(75.0)  # type: ignore[arg-type]

    def test_below_zero_raises(self):
        with self.assertRaises(BandError):
            assign_band(-1)

    def test_above_100_raises(self):
        with self.assertRaises(BandError):
            assign_band(101)


class SerializationTests(unittest.TestCase):
    def test_band_to_dict_shape(self):
        d = band_to_dict(assign_band(62))
        self.assertEqual(d["letter"], "C")
        self.assertEqual(d["modifier"], "neutral")
        self.assertEqual(d["display"], "C")
        self.assertAlmostEqual(d["modifier_factor"], 1.00)
        self.assertIn("Surplus", d["posture"])
        self.assertEqual(d["bands_version"], "1.0")


if __name__ == "__main__":
    unittest.main()
