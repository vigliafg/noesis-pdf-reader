"""Benchmark engines — three Docling-based Markdown extractors + image helpers.

Each engine is exposed as a ``extract_*(pdf_path, page_1based) -> str`` function
so the benchmark treats them uniformly.

- ``extract_docling_low_overhead`` : pdfextractor-style (A) — one reused converter,
  CPU/1-thread/batch-1, generate_picture_images, de-hyphenation.
- ``extract_docling_current``     : noesis-pdf-reader current code (B) — a fresh
  ``DocumentConverter()`` per page, no options, no cleanup.
- ``extract_granite_docling``     : granite-docling VLM pipeline (C) — reused
  converter, model loaded once.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    AcceleratorDevice,
    AcceleratorOptions,
    PdfPipelineOptions,
    VlmPipelineOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.pipeline.vlm_pipeline import VlmPipeline
from docling_core.types.doc import PictureItem


# ─────────────────────────────────────────────────────────────────────────────
#  shared: de-hyphenation (ported from pdfextractor/pdf_to_md/converter.py)
# ─────────────────────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    """Remove end-of-line hyphenation: "com-\\npany" -> "company"."""
    return re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)


# ─────────────────────────────────────────────────────────────────────────────
#  engine A — docling low-overhead (pdfextractor style)
# ─────────────────────────────────────────────────────────────────────────────

_A_CONVERTER: DocumentConverter | None = None
_A_CONVERTER_ERROR: str | None = None


def _make_low_overhead_converter() -> DocumentConverter:
    pipeline_options = PdfPipelineOptions()
    pipeline_options.accelerator_options = AcceleratorOptions(
        num_threads=1, device=AcceleratorDevice.CPU
    )
    pipeline_options.ocr_batch_size = 1
    pipeline_options.layout_batch_size = 1
    pipeline_options.table_batch_size = 1
    pipeline_options.images_scale = 2.0
    pipeline_options.generate_picture_images = True
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def get_low_overhead_converter() -> DocumentConverter:
    """Return (and cache) the single low-overhead converter instance."""
    global _A_CONVERTER
    if _A_CONVERTER is None:
        _A_CONVERTER = _make_low_overhead_converter()
    return _A_CONVERTER


def extract_docling_low_overhead(pdf_path: str | Path, page: int) -> str:
    """Engine A: one reused converter, per-page range, de-hyphenation applied."""
    converter = get_low_overhead_converter()
    result = converter.convert(str(pdf_path), page_range=(page, page))
    md = result.document.export_to_markdown()
    return clean_text(md.strip() or "(nessun testo estraibile)")


def extract_docling_low_overhead_document(pdf_path: str | Path) -> str:
    """Engine A on a whole (small) document in a single pass, de-hyphenated."""
    converter = get_low_overhead_converter()
    result = converter.convert(str(pdf_path))
    return clean_text(result.document.export_to_markdown())


# ─────────────────────────────────────────────────────────────────────────────
#  engine B — docling current (noesis-pdf-reader `_extract_docling` verbatim)
# ─────────────────────────────────────────────────────────────────────────────

def extract_docling_current(pdf_path: str | Path, page: int) -> str:
    """Engine B: fresh converter every page, no options, no cleanup."""
    converter = DocumentConverter()
    result = converter.convert(str(pdf_path), page_range=(page, page))
    return result.document.export_to_markdown().strip() or "(nessun testo estraibile)"


# ─────────────────────────────────────────────────────────────────────────────
#  engine C — granite-docling (VLM pipeline)
# ─────────────────────────────────────────────────────────────────────────────

_C_CONVERTER: DocumentConverter | None = None


def _make_granite_converter() -> DocumentConverter:
    """Build the granite-docling VLM converter (default auto_inline engine)."""
    pipeline_options = VlmPipelineOptions()
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=VlmPipeline, pipeline_options=pipeline_options
            )
        }
    )


def _make_granite_converter_cpu_fallback() -> DocumentConverter:
    """Explicit transformers engine with ``load_in_8bit=False`` for CPU."""
    from docling.datamodel import vlm_model_specs

    inline = vlm_model_specs.GRANITEDOCLING_TRANSFORMERS.model_copy(
        update={"load_in_8bit": False}
    )
    pipeline_options = VlmPipelineOptions(vlm_options=inline)
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=VlmPipeline, pipeline_options=pipeline_options
            )
        }
    )


def get_granite_converter() -> DocumentConverter:
    global _C_CONVERTER
    if _C_CONVERTER is None:
        _C_CONVERTER = _make_granite_converter()
    return _C_CONVERTER


def extract_granite_docling(pdf_path: str | Path, page: int) -> str:
    """Engine C: granite-docling VLM, converter reused, per-page."""
    converter = get_granite_converter()
    try:
        result = converter.convert(str(pdf_path), page_range=(page, page))
    except Exception as e:
        msg = str(e)
        # CPU fallback: 8-bit loading needs bitsandbytes/CUDA.
        if any(k in msg.lower() for k in ("bitsandbytes", "8bit", "8-bit", "cuda")):
            global _C_CONVERTER
            _C_CONVERTER = _make_granite_converter_cpu_fallback()
            result = _C_CONVERTER.convert(str(pdf_path), page_range=(page, page))
        else:
            raise
    return result.document.export_to_markdown().strip() or "(nessun testo estraibile)"


# ─────────────────────────────────────────────────────────────────────────────
#  image extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_images_docling(
    pdf_path: str | Path, page: int, out_dir: str | Path, prefix: str = ""
) -> list[str]:
    """Engine A image strategy: Docling ``PictureItem`` crops (whole figures)."""
    from PIL import Image

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    converter = get_low_overhead_converter()
    result = converter.convert(str(pdf_path), page_range=(page, page))

    saved: list[str] = []
    for element, _level in result.document.iterate_items():
        if not isinstance(element, PictureItem):
            continue
        try:
            img = element.get_image(result.document)
        except Exception:
            continue
        if img is None:
            continue
        name = f"{prefix}page_{page:04d}_img_{len(saved)}.png"
        path = out_dir / name
        img.convert("RGB").save(path)
        saved.append(str(path))
    return saved


def extract_images_pymupdf(
    pdf_path: str | Path, page: int, out_dir: str | Path, prefix: str = ""
) -> list[str]:
    """Current image strategy: raw XObjects via pymupdf (decomposes figures)."""
    import pymupdf

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))
    try:
        page_obj = doc[page - 1]
        saved: list[str] = []
        for xref, *_ in page_obj.get_images(full=True):
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            ext = info.get("ext", "png")
            name = f"{prefix}page_{page:04d}_xref{xref}.{ext}"
            path = out_dir / name
            path.write_bytes(info["image"])
            saved.append(str(path))
        return saved
    finally:
        doc.close()
