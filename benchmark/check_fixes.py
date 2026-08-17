#!/usr/bin/env python3
"""Check the reading-order fixes for pymupdf4llm and docling on random pages.

For each sampled page that has a two-column layout, four candidate outputs are
compared against *geometry-derived* anchors (top/bottom of the left and right
columns, taken from pymupdf block coordinates — the ground-truth reading order
is left column top→bottom, then right column top→bottom):

- ``pymupdf4llm-raw``  : pymupdf4llm.to_markdown (no fix)
- ``pymupdf4llm-fix``  : main._column_aware_markdown (the column reorder fix)
- ``docling-raw``      : raw docling export (no fix)
- ``docling-fix``      : docling + de-hyphenation + cross-column re-split

Usage (from project root)::

    .venv/bin/python -m benchmark.check_fixes --pages 10 --after 500
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf  # noqa: E402
import pymupdf4llm  # noqa: E402

import main as app  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  docling (reused converter, mirroring main.py)
# ─────────────────────────────────────────────────────────────────────────────

_CONVERTER = None


def _get_converter():
    global _CONVERTER
    if _CONVERTER is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        pipeline_options = PdfPipelineOptions()
        pipeline_options.images_scale = 2.0
        pipeline_options.generate_picture_images = True
        _CONVERTER = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
    return _CONVERTER


def _extract_docling_raw(pdf_path: Path, page_1based: int) -> str:
    result = _get_converter().convert(str(pdf_path), page_range=(page_1based, page_1based))
    return result.document.export_to_markdown().strip()


def _extract_docling_fix(pdf_path: Path, page_1based: int) -> str:
    md = app.clean_text(_extract_docling_raw(pdf_path, page_1based)).strip()
    doc = pymupdf.open(str(pdf_path))
    try:
        return app._split_cross_column_paragraphs(md, doc[page_1based - 1])
    finally:
        doc.close()


# ─────────────────────────────────────────────────────────────────────────────
#  geometry anchors + order check
# ─────────────────────────────────────────────────────────────────────────────

def _dehyphen_join(tokens: list[str]) -> str:
    """Join raw span tokens, fusing hyphenated line-breaks (patho- + gens → pathogens)."""
    out: list[str] = []
    for t in tokens:
        if out and out[-1].endswith("-") and len(out[-1]) > 1:
            out[-1] = out[-1][:-1] + t
        else:
            out.append(t)
    return " ".join(out)


def _norm_anchor(s: str) -> str:
    """Robust normalizer for order matching: lowercase, de-hyphenate, strip punct."""
    s = s.lower().replace("-", "")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _block_text(b: dict) -> str:
    return _dehyphen_join(s["text"] for line in b["lines"] for s in line)


def _is_content(b: dict) -> bool:
    """True for real body text; drops page numbers, margin labels, footnotes."""
    t = _block_text(b)
    if len(t) < 25 or len(t.split()) < 4:
        return False
    if all(re.fullmatch(r"[\d\W]+", w) for w in t.split()):
        return False
    w, h = b["x1"] - b["x0"], b["y1"] - b["y0"]
    if w < 30:            # gutter labels / rotated side labels / page numbers
        return False
    if h > 2.5 * w:       # rotated (vertical) text in the margin
        return False
    return True


def _geometry_anchors(page) -> list[str]:
    """Return [first_top, first_bottom, last_top] phrases, or [] if not multi-column.

    Works for any column count. The reading-order signal is: the first column
    is read top→bottom, then the next column to the right, … so the critical
    check is ``first_bottom`` appearing *before* ``last_top``. Table cells are
    excluded first (the fix renders them separately as markdown tables).
    """
    blocks = app._collect_blocks(page)
    # Exclude data-table cells, mirroring the fix's own rule.
    table_regions: list[tuple] = []
    try:
        tabs = page.find_tables()
    except Exception:
        tabs = None
    if tabs:
        for t in tabs.tables:
            if t.row_count <= 1 and t.col_count <= 2:
                continue
            table_regions.append(tuple(t.bbox))

    def _inside(b: dict, r: tuple) -> bool:
        return (
            b["x0"] >= r[0] - 2 and b["x1"] <= r[2] + 2
            and b["y0"] >= r[1] - 2 and b["y1"] <= r[3] + 2
        )

    blocks = [
        b for b in blocks
        if not any(_inside(b, r) for r in table_regions)
        and (b["x1"] - b["x0"]) < 0.6 * page.rect.width  # column-width only
    ]
    # Headers/footers/watermarks are not content: keep them out of both the
    # split detection and the anchor selection (same rule as the engine).
    blocks = app._strip_margin_blocks(blocks, page.rect.height)
    splits = app._detect_column_splits(blocks, page.rect.width)
    if not splits:
        return []

    def _col(b: dict) -> int:
        return sum(1 for s in splits if (b["x0"] + b["x1"]) / 2 > s)

    n = len(splits) + 1
    cols: list[list[dict]] = [[] for _ in range(n)]
    for b in blocks:
        if _is_content(b):
            cols[_col(b)].append(b)
    cols = [c for c in cols if c]
    if len(cols) < 2 or len(cols[0]) < 2 or len(cols[-1]) < 2:
        return []

    def _phrase(b: dict, head: bool) -> str:
        toks = [s["text"] for line in b["lines"] for s in line]
        toks = _dehyphen_join(toks).split()
        window = toks[:8] if head else toks[-8:]
        return " ".join(window)

    first, last = cols[0], cols[-1]
    ftop = min(first, key=lambda b: b["y0"])
    fbot = max(first, key=lambda b: b["y1"])
    ltop = min(last, key=lambda b: b["y0"])
    return [_phrase(ftop, True), _phrase(fbot, False), _phrase(ltop, True)]


def _check_order(md: str, anchors: list[str]) -> dict:
    norm = _norm_anchor(md)
    positions: list[int | None] = []
    for a in anchors:
        p = norm.find(_norm_anchor(a))
        positions.append(p if p >= 0 else None)
    found = [p for p in positions if p is not None]
    # The essential signal: the left column's last body text precedes the
    # right column's first body text (left column read fully before right).
    column_ok = (
        positions[1] is not None
        and positions[2] is not None
        and positions[1] < positions[2]
    )
    return {
        "positions": positions,
        "missing": sum(1 for p in positions if p is None),
        "column_ok": column_ok,
        "in_order": len(found) == len(anchors) and found == sorted(found),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", default="harrison2025.pdf")
    ap.add_argument("--pages", type=int, default=10, help="how many pages to sample")
    ap.add_argument("--after", type=int, default=500, help="sample only pages > this")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDF non trovato: {pdf_path}", file=sys.stderr)
        return 1

    doc = pymupdf.open(str(pdf_path))
    total = doc.page_count
    pool = list(range(max(args.after, 0), total))
    random.seed(args.seed)
    pages_1based = sorted(random.sample(pool, min(args.pages, len(pool))))

    variants = {
        "pymupdf4llm-raw": lambda p: pymupdf4llm.to_markdown(str(pdf_path), pages=[p - 1]),
        "pymupdf4llm-fix": lambda p: app._column_aware_markdown(doc[p - 1]),
        "docling-raw": lambda p: _extract_docling_raw(pdf_path, p),
        "docling-fix": lambda p: _extract_docling_fix(pdf_path, p),
    }

    print(f"PDF: {pdf_path.name} ({total} pagine). Campione {len(pages_1based)} pagine > {args.after}: {pages_1based}\n")

    summary = {k: {"two_col": 0, "col_ok": 0, "in_order": 0, "missing": 0} for k in variants}
    two_col_pages = 0
    for p in pages_1based:
        anchors = _geometry_anchors(doc[p - 1])
        if not anchors:
            print(f"p.{p}: monocolonna (fix non applicabile) — skip")
            continue
        two_col_pages += 1
        print(f"p.{p}: DUE COLONNE — ancore: {[a[:22] + '…' for a in anchors]}")
        for name, fn in variants.items():
            t0 = time.perf_counter()
            md = fn(p)
            res = _check_order(md, anchors)
            secs = time.perf_counter() - t0
            summary[name]["two_col"] += 1
            summary[name]["col_ok"] += res["column_ok"]
            summary[name]["in_order"] += res["in_order"]
            summary[name]["missing"] += res["missing"]
            print(
                f"    {name:16s} colonna_ok={res['column_ok']!s:5s} mancanti={res['missing']} "
                f"pos={res['positions']} ({secs:.1f}s)"
            )
        print()
    doc.close()

    print("=" * 70)
    print("RIEPILOGO (pagine a due colonne: %d)" % two_col_pages)
    print(f"{'variante':16s} {'colonna ok':>12s} {'ordine ok':>11s} {'frasi mancanti':>15s}")
    for name in variants:
        s = summary[name]
        print(f"{name:16s} {s['col_ok']:>4d}/{s['two_col']:>6d} {s['in_order']:>4d}/{s['two_col']:>5d} {s['missing']:>15d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
