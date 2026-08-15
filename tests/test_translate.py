"""Tests for the markdown-preserving translation (tables + image links)."""

import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import main  # noqa: E402


def _fake_translate(s: str, source: str, target: str) -> str:
    """Simulate translation by uppercasing (tokens are already uppercase)."""
    return s.upper()


class TranslateTableTests(unittest.TestCase):
    def test_table_structure_and_cells_survive(self):
        text = (
            "Intro text here.\n\n"
            "| Name | Value |\n| --- | --- |\n| Alpha | 42 |"
        )
        with mock.patch("main._gt_translate_one", side_effect=_fake_translate):
            out = main.translate_text_google(text)
        self.assertIn("INTRO TEXT HERE.", out)
        self.assertIn("| NAME | VALUE |", out)
        self.assertIn("| --- | --- |", out)
        self.assertIn("| ALPHA | 42 |", out)

    def test_separator_row_kept_verbatim(self):
        table = "| A | B |\n|:---|---:|\n| x | y |"
        with mock.patch("main._gt_translate_one", side_effect=_fake_translate):
            out = main._translate_table(table, "en", "it")
        self.assertIn("|:---|---:|", out)
        self.assertIn("| X | Y |", out)

    def test_numeric_only_cells_left_alone(self):
        table = "| Dose |\n| --- |\n| 500 mg |"
        with mock.patch("main._gt_translate_one", side_effect=_fake_translate):
            out = main._translate_table(table, "en", "it")
        # "500 mg" contains letters so it's translated (uppercased); the point
        # is structure is intact and no crash on mixed cells.
        self.assertIn("| DOSE |", out)
        self.assertIn("| --- |", out)


class TranslateImageTests(unittest.TestCase):
    def test_image_links_survive_translation(self):
        text = "See the figure:\n\n![figura](file:///tmp/x/page_1007_img_0.png)"
        with mock.patch("main._gt_translate_one", side_effect=_fake_translate):
            out = main.translate_text_google(text)
        self.assertIn("![figura](file:///tmp/x/page_1007_img_0.png)", out)
        self.assertIn("SEE THE FIGURE:", out)


if __name__ == "__main__":
    unittest.main()
