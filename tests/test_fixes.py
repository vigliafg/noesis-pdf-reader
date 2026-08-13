"""Tests for the generic layout fixes (columns, title, markdown tables).

These tests exercise the pure helpers (``_spacing_fixes``,
``_detect_column_split``, ``_block_to_md``) directly, and the page-level
functions (``_table_to_md``, ``_column_aware_markdown``) against small
synthetic PDF pages built with pymupdf.  A regression test against the real
``harrison2025.pdf`` is included but skipped when the file is absent.

Run with (from the project root):

    .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

# Make the single-file `main` module importable from the project root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pymupdf  # noqa: E402
import markdown  # noqa: E402

import main  # noqa: E402
from main import (  # noqa: E402
    _MD_EXTENSIONS,
    _block_to_md,
    _collect_blocks,
    _column_aware_markdown,
    _detect_column_split,
    _spacing_fixes,
    _table_to_md,
)

PAGE_W = 595
PAGE_H = 842

# The two text columns used across synthetic fixtures.
LEFT = (50, 250)
RIGHT = (320, 520)

REAL_PDF = os.path.join(_ROOT, "harrison2025.pdf")


def _new_page(width=PAGE_W, height=PAGE_H):
    """Return a fresh (document, page) pair backed by an in-memory PDF."""
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    return doc, page


def _add_table(page, rect, rows, fontsize=9):
    """Draw a grid + its cell text so ``find_tables`` detects a real table."""
    x0, y0, x1, y1 = rect
    nrows, ncols = len(rows), len(rows[0])
    col_w = (x1 - x0) / ncols
    row_h = (y1 - y0) / nrows
    page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=(0, 0, 0), width=1)
    for c in range(1, ncols):
        page.draw_line(
            pymupdf.Point(x0 + c * col_w, y0),
            pymupdf.Point(x0 + c * col_w, y1),
            color=(0, 0, 0),
            width=1,
        )
    for r in range(1, nrows):
        page.draw_line(
            pymupdf.Point(x0, y0 + r * row_h),
            pymupdf.Point(x1, y0 + r * row_h),
            color=(0, 0, 0),
            width=1,
        )
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            cell_rect = pymupdf.Rect(
                x0 + c * col_w + 3,
                y0 + r * row_h + 3,
                x0 + (c + 1) * col_w - 3,
                y0 + (r + 1) * row_h - 3,
            )
            page.insert_textbox(cell_rect, str(cell), fontsize=fontsize)
    return page


def _add_captioned_table(page, rect, caption, header, data_rows, fontsize=9):
    """Draw a table whose first (full-width) row is a caption."""
    x0, y0, x1, y1 = rect
    ncols = len(header)
    # Rows: caption (0), header (1), then one per data row.
    nrows = 2 + len(data_rows)
    row_h = (y1 - y0) / nrows
    row_ys = [y0 + i * row_h for i in range(nrows + 1)]
    page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=(0, 0, 0), width=1)
    for y in row_ys[1:-1]:
        page.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x1, y), color=(0, 0, 0), width=1)
    # Vertical rules only below the caption row.
    col_w = (x1 - x0) / ncols
    for c in range(1, ncols):
        page.draw_line(
            pymupdf.Point(x0 + c * col_w, row_ys[1]),
            pymupdf.Point(x0 + c * col_w, y1),
            color=(0, 0, 0),
            width=1,
        )
    page.insert_textbox(
        pymupdf.Rect(x0 + 3, row_ys[0] + 3, x1 - 3, row_ys[1] - 3),
        caption,
        fontsize=fontsize,
    )
    for c, h in enumerate(header):
        page.insert_textbox(
            pymupdf.Rect(x0 + c * col_w + 3, row_ys[1] + 3, x0 + (c + 1) * col_w - 3, row_ys[2] - 3),
            str(h),
            fontsize=fontsize,
        )
    for r, row in enumerate(data_rows):
        for c, cell in enumerate(row):
            page.insert_textbox(
                pymupdf.Rect(
                    x0 + c * col_w + 3,
                    row_ys[2 + r] + 3,
                    x0 + (c + 1) * col_w - 3,
                    row_ys[3 + r] - 3,
                ),
                str(cell),
                fontsize=fontsize,
            )
    return page


class SpacingFixesTests(unittest.TestCase):
    def test_bold_cross_reference_glued_to_word(self):
        self.assertEqual(_spacing_fixes("**134**and pathogen"), "**134** and pathogen")

    def test_italic_comma_glued_to_next_word(self):
        self.assertEqual(
            _spacing_fixes("_S. aureus_,_Streptococcus_"),
            "_S. aureus_, _Streptococcus_",
        )

    def test_clean_text_is_unchanged(self):
        text = "Normal **bold** text with spaces, and _italics_."
        self.assertEqual(_spacing_fixes(text), text)


class DetectColumnSplitTests(unittest.TestCase):
    @staticmethod
    def _block(x0, x1):
        return {"x0": x0, "y0": 0, "x1": x1, "y1": 10, "max_size": 10.0, "lines": []}

    def test_two_columns_detected(self):
        blocks = [
            self._block(50, 200),
            self._block(55, 190),
            self._block(320, 470),
            self._block(330, 460),
        ]
        split = _detect_column_split(blocks, PAGE_W)
        self.assertIsNotNone(split)
        self.assertTrue(190 < split < 320)

    def test_single_column_returns_none(self):
        blocks = [self._block(50, 200), self._block(55, 190)]
        self.assertIsNone(_detect_column_split(blocks, PAGE_W))


class BlockToMdTests(unittest.TestCase):
    @staticmethod
    def _block(size, lines):
        return {"x0": 0, "y0": 0, "x1": 100, "y1": 10, "max_size": size, "lines": lines}

    def test_heading_levels(self):
        b = self._block(16, [[{"text": "Chapter One", "size": 16, "bold": False, "italic": False}]])
        self.assertEqual(_block_to_md(b, as_column=True), "# Chapter One")

        b = self._block(12, [[{"text": "Section", "size": 12, "bold": False, "italic": False}]])
        self.assertEqual(_block_to_md(b, as_column=True), "## Section")

    def test_bold_and_italic_spans(self):
        b = self._block(
            10,
            [[
                {"text": "a", "size": 10, "bold": True, "italic": False},
                {"text": " ", "size": 10, "bold": False, "italic": False},
                {"text": "b", "size": 10, "bold": False, "italic": True},
            ]],
        )
        self.assertEqual(_block_to_md(b, as_column=False), "**a** *b*")


class TableToMdTests(unittest.TestCase):
    def test_table_rendered_as_markdown(self):
        doc, page = _new_page()
        _add_table(
            page,
            (50, 80, 300, 160),
            [["Name", "Value"], ["Alpha", "42"], ["Beta", "7"]],
        )
        tabs = page.find_tables()
        self.assertEqual(len(tabs.tables), 1)
        md = _table_to_md(page, tabs.tables[0])
        self.assertIn("| Name | Value |", md)
        self.assertIn("| --- | --- |", md)
        self.assertIn("| Alpha | 42 |", md)
        self.assertIn("| Beta | 7 |", md)
        doc.close()

    def test_caption_keeps_blank_line_so_table_renders(self):
        # Regression: a caption glued to the header row (no blank line) made
        # python-markdown's "tables" extension skip the whole table.
        doc, page = _new_page()
        _add_captioned_table(
            page,
            (50, 80, 300, 190),
            "TABLE 1 My Table",
            ["A", "B"],
            [["1", "2"]],
        )
        tabs = page.find_tables()
        self.assertEqual(len(tabs.tables), 1)
        md = _table_to_md(page, tabs.tables[0])
        self.assertIn("**TABLE 1 My Table**\n\n| A | B |", md)
        self.assertIn("<table>", markdown.markdown(md, extensions=_MD_EXTENSIONS))
        doc.close()


class ColumnAwareTests(unittest.TestCase):
    @staticmethod
    def _two_column_page():
        """Left intro, a chapter title in the middle, then more left text.

        The title sits mid-column so ``move_title`` has an observable effect.
        """
        doc, page = _new_page()
        page.insert_textbox(pymupdf.Rect(50, 120, 250, 200), "Left intro para.", fontsize=10)
        page.insert_textbox(pymupdf.Rect(50, 240, 250, 270), "Chapter Title", fontsize=16)
        page.insert_textbox(pymupdf.Rect(50, 300, 250, 380), "Left after para.", fontsize=10)
        page.insert_textbox(pymupdf.Rect(320, 120, 520, 200), "Right intro para.", fontsize=10)
        page.insert_textbox(pymupdf.Rect(320, 300, 520, 380), "Right after para.", fontsize=10)
        return page

    def test_left_column_read_before_right(self):
        page = self._two_column_page()
        md = _column_aware_markdown(page, move_title=False)
        self.assertLess(md.index("Left intro para."), md.index("Right intro para."))
        self.assertLess(md.index("Left after para."), md.index("Right after para."))

    def test_title_moved_to_top(self):
        page = self._two_column_page()
        md = _column_aware_markdown(page, move_title=True)
        self.assertTrue(md.startswith("# Chapter Title"))
        # Without the fix the title stays in the middle of the left column.
        md_plain = _column_aware_markdown(page, move_title=False)
        self.assertLess(md_plain.index("Left intro para."), md_plain.index("Chapter Title"))

    def test_middle_table_is_kept_between_bands(self):
        doc, page = _new_page()
        page.insert_textbox(pymupdf.Rect(50, 120, 250, 200), "Left above.", fontsize=10)
        page.insert_textbox(pymupdf.Rect(320, 120, 520, 200), "Right above.", fontsize=10)
        _add_table(page, (50, 400, 545, 460), [["Name", "Value"], ["Alpha", "42"]])
        page.insert_textbox(pymupdf.Rect(50, 500, 250, 580), "Left below.", fontsize=10)
        page.insert_textbox(pymupdf.Rect(320, 500, 520, 580), "Right below.", fontsize=10)

        md = _column_aware_markdown(page, move_title=False)
        self.assertIn("| --- | --- |", md)
        self.assertIn("| Alpha | 42 |", md)
        # The table sits after the "above" band and before the "below" band.
        self.assertLess(md.index("Left above."), md.index("| --- | --- |"))
        self.assertLess(md.index("| Alpha | 42 |"), md.index("Left below."))
        doc.close()


@unittest.skipUnless(os.path.exists(REAL_PDF), "harrison2025.pdf not present")
class RealPdfRegressionTests(unittest.TestCase):
    """Regression checks on the pages that originally motivated the fixes."""

    @classmethod
    def setUpClass(cls):
        cls.doc = pymupdf.open(REAL_PDF)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_page_1007_intro_paragraph_in_reading_order(self):
        md = _column_aware_markdown(self.doc[1006], move_title=False)
        self.assertLess(
            md.index("corresponding human"), md.index("cellular responses")
        )

    def test_page_156_table_rendered_as_markdown(self):
        md = _column_aware_markdown(self.doc[155], move_title=False)
        self.assertIn("| ---", md)


if __name__ == "__main__":
    unittest.main()
