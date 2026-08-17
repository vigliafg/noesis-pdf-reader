#!/usr/bin/env python3
"""Verify the bordered-box fix across the test PDFs.

For each PDF, sample ``--pages`` random pages (from ``--after`` onwards, fixed
seed) and check that, for every page:

- ``_column_aware_markdown`` does not raise;
- every detected box is rendered as a single-column markdown table;
- the box's text appears exactly once in the output (not lost, not duplicated
  in the body flow);
- the multi-column reading order is preserved (geometry anchors, as in
  ``check_fixes.py``).

Usage (from the project root)::

    .venv/bin/python -m benchmark.check_boxes --pages 10 --after 200 --seed 42
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf  # noqa: E402

import main as app  # noqa: E402
from benchmark.check_fixes import _check_order, _geometry_anchors  # noqa: E402

DEFAULT_PDFS = (
    "harrison2025.pdf,cecil2024.pdf,oxford2020.pdf,"
    "rosen2022.pdf,andrew2020.pdf,robbins2021.pdf,seidel2015.pdf,mayo2020.pdf"
)


def _norm(s: str) -> str:
    return " ".join(s.split()).strip()


def _table_regions(page) -> list[tuple]:
    """Data-table bboxes, same filter as _column_aware_markdown."""
    regions: list[tuple] = []
    try:
        tabs = page.find_tables()
    except Exception:
        return regions
    for t in tabs.tables:
        if t.row_count <= 1 and t.col_count <= 2:
            continue
        regions.append(tuple(t.bbox))
    return regions


def check_page(page) -> dict:
    """Return {boxes, issues, order} for one page."""
    # Boxes as _column_aware_markdown would process them (excludes table cells).
    boxes = app._detect_boxes(page, page.rect.width, _table_regions(page))
    issues: list[str] = []
    try:
        md = app._column_aware_markdown(page)
    except Exception as e:  # noqa: BLE001
        return {"boxes": len(boxes), "issues": [f"eccezione: {e!r}"], "order": None}

    for bx in boxes:
        rows = [ln.strip() for ln in bx["md"].splitlines() if ln.strip()]
        if not rows:
            issues.append("box senza markdown")
            continue
        # The rendered table must appear in the output (not lost).
        if bx["md"] not in md:
            issues.append("box non emesso nell'output")
        if "| --- |" not in bx["md"]:
            issues.append("box non reso come tabella (manca | --- |)")
        # Duplication: only significant lines (>= 4 words) — short labels and
        # page numbers legitimately appear elsewhere on the page.
        for ln in rows:
            if ln in ("| --- |", ""):
                continue
            inner = _norm(ln.strip("|").strip())
            if len(inner.split()) < 4:
                continue
            n = md.count(inner)
            if n > 1:
                issues.append(f"testo box duplicato x{n}: {inner[:40]!r}")

    order = None
    anchors = _geometry_anchors(page)
    if anchors:
        order = _check_order(md, anchors)
    return {"boxes": len(boxes), "issues": issues, "order": order}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdfs", default=DEFAULT_PDFS)
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--after", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    grand = {"pages": 0, "box_pages": 0, "boxes": 0, "box_ok": 0,
             "lost": 0, "dup": 0, "not_table": 0, "errors": 0,
             "multi": 0, "order_ok": 0}

    for name in args.pdfs.split(","):
        pdf_path = Path(name.strip())
        if not pdf_path.exists():
            print(f"PDF non trovato: {pdf_path}", file=sys.stderr)
            continue
        doc = pymupdf.open(str(pdf_path))
        total = doc.page_count
        pool = list(range(max(args.after, 0), total))
        if not pool:
            pool = list(range(total))
        random.seed(args.seed)
        pages = sorted(random.sample(pool, min(args.pages, len(pool))))

        pdf = {"pages": 0, "box_pages": 0, "boxes": 0, "box_ok": 0,
               "lost": 0, "dup": 0, "not_table": 0, "errors": 0,
               "multi": 0, "order_ok": 0}
        t0 = time.perf_counter()
        for p in pages:
            r = check_page(doc[p - 1])
            pdf["pages"] += 1
            grand["pages"] += 1
            if r["issues"]:
                pdf["errors"] += 1
                grand["errors"] += 1
                print(f"  ! p.{p} [{name}]: {r['issues'][:3]}")
            if r["boxes"]:
                pdf["box_pages"] += 1
                grand["box_pages"] += 1
            pdf["boxes"] += r["boxes"]
            grand["boxes"] += r["boxes"]
            if not r["issues"] and r["boxes"]:
                pdf["box_ok"] += r["boxes"]
                grand["box_ok"] += r["boxes"]
            if r["order"]:
                pdf["multi"] += 1
                grand["multi"] += 1
                if r["order"]["column_ok"]:
                    pdf["order_ok"] += 1
                    grand["order_ok"] += 1
        doc.close()

        print(
            f"### {name} ({total} pagine, campione {len(pages)}, {time.perf_counter()-t0:.0f}s)"
        )
        print(
            f"  pagine con box: {pdf['box_pages']}/{pdf['pages']}  "
            f"box totali: {pdf['boxes']}  box ok: {pdf['box_ok']}  "
            f"pagine con problemi: {pdf['errors']}"
        )
        if pdf["multi"]:
            print(f"  ordine lettura multi-colonna: {pdf['order_ok']}/{pdf['multi']}")

    print("=" * 70)
    print("RIEPILOGO TOTALE")
    print(f"pagine: {grand['pages']}  pagine con box: {grand['box_pages']}  "
          f"box: {grand['boxes']}  box ok: {grand['box_ok']}  "
          f"pagine con problemi: {grand['errors']}")
    if grand["multi"]:
        print(f"ordine lettura multi-colonna: {grand['order_ok']}/{grand['multi']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
