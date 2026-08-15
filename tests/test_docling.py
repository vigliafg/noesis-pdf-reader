"""Tests for the Docling backend additions (de-hyphenation + image links)."""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import markdown  # noqa: E402

from main import _MD_EXTENSIONS, clean_text  # noqa: E402


class CleanTextTests(unittest.TestCase):
    def test_dehyphenates_line_breaks(self):
        self.assertEqual(clean_text("This is a com-\npany."), "This is a company.")

    def test_joins_hyphen_across_blank_line(self):
        self.assertEqual(clean_text("sci-\n\nentific"), "scientific")

    def test_leaves_normal_hyphens_alone(self):
        text = "well-known phrase and a-b c."
        self.assertEqual(clean_text(text), text)

    def test_leaves_non_word_hyphenation_alone(self):
        text = "a - b\nc"
        self.assertEqual(clean_text(text), text)


class ImageLinkRenderingTests(unittest.TestCase):
    def test_file_uri_image_becomes_img_tag(self):
        md = "![figura](file:///tmp/noesis-pdf-reader/x/page_1007_img_0.png)"
        html = markdown.markdown(md, extensions=_MD_EXTENSIONS)
        self.assertIn(
            '<img alt="figura" src="file:///tmp/noesis-pdf-reader/x/page_1007_img_0.png" />',
            html,
        )


if __name__ == "__main__":
    unittest.main()
