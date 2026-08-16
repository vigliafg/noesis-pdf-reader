"""Tests for the PyMuPDF image extraction helpers (automatic + region).

These exercise the pure module-level helpers ``_page_embedded_images`` and
``_region_image`` against a small synthetic PDF page with an embedded PNG,
so no GUI is required.
"""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pymupdf  # noqa: E402

from main import _page_embedded_images, _region_image  # noqa: E402

PNG_SIG = b"\x89PNG"


def _make_doc_with_image():
    """Return (doc, page, png_bytes) with one embedded PNG at (50,50,150,150)."""
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 20))
    pix.clear_with(250)
    png = pix.tobytes("png")
    page.insert_image(pymupdf.Rect(50, 50, 150, 150), stream=png)
    return doc, page, png


class PageEmbeddedImagesTests(unittest.TestCase):
    def test_extracts_embedded_image_bytes(self):
        doc, _page, _png = _make_doc_with_image()
        try:
            imgs = _page_embedded_images(doc, 0)
            self.assertEqual(len(imgs), 1)
            data, ext = imgs[0]
            self.assertEqual(ext, "png")
            self.assertTrue(data.startswith(PNG_SIG))
        finally:
            doc.close()

    def test_empty_page_returns_no_images(self):
        doc = pymupdf.open()
        doc.new_page(width=100, height=100)
        try:
            self.assertEqual(_page_embedded_images(doc, 0), [])
        finally:
            doc.close()


class RegionImageTests(unittest.TestCase):
    def test_full_cover_returns_embedded_bytes(self):
        doc, _page, _png = _make_doc_with_image()
        try:
            embedded = _page_embedded_images(doc, 0)
            self.assertEqual(len(embedded), 1)
            out = _region_image(doc, 0, (40, 40, 160, 160), 2.0)
            self.assertIsNotNone(out)
            self.assertEqual(out, embedded[0])  # original raster, no re-render
        finally:
            doc.close()

    def test_subregion_falls_back_to_render(self):
        doc, _page, _png = _make_doc_with_image()
        try:
            embedded = _page_embedded_images(doc, 0)[0]
            out = _region_image(doc, 0, (60, 60, 80, 80), 2.0)
            self.assertIsNotNone(out)
            data, ext = out
            self.assertEqual(ext, "png")
            self.assertTrue(data.startswith(PNG_SIG))
            self.assertNotEqual(data, embedded[0])  # re-rendered, not original
        finally:
            doc.close()

    def test_empty_region_returns_none(self):
        doc, _page, _png = _make_doc_with_image()
        try:
            self.assertIsNone(_region_image(doc, 0, (10, 10, 5, 5), 2.0))
        finally:
            doc.close()


if __name__ == "__main__":
    unittest.main()
