#!/usr/bin/env python3
"""Verify the FULL adaptive engine (profile → plan → pipeline) on test PDFs.

For each sampled page (pymupdf4llm backend), it runs::

    profile = layout_engine.profile_page(page)
    plan    = layout_engine.plan_fixes(profile, "PyMuPDF4LLM ⚡", "auto")
    md      = layout_engine.apply_plan(raw, page, profile, plan)

and checks:

- nothing raises;
- the multi-column reading order is correct (geometry anchors);
- boxes are rendered as markdown tables and their text is not lost/duplicated;
- the profile/plan are printed, so unusual layouts (e.g. David's complex
  sequence) are visible.

Usage::

    .venv/bin/python -m benchmark.check_engine --pdfs eco2022.pdf,david2027.pdf \
        --pages 10 --after 200 --seed 42
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf  # noqa: E402
import pymupdf4llm  # noqa: E402

import layout_engine  # noqa: E402
import main as app  # noqa: E402
from benchmark.check_fixes import _check_order, _geometry_anchors  # noqa: E402
from benchmark.check_boxes import _norm, _table_regions  # noqa: E402
from benchmark.metrics import normalize  # noqa: E402


def run_engine(page, pdf_path: Path, page_1based: int):
    """Run the full engine and return (profile, plan, raw, md)."""
    raw = pymupdf4llm.to_markdown(str(pdf_path), pages=[page_1based - 1])
    profile = layout_engine.profile_page(page)
    plan = layout_engine.plan_fixes(profile, "PyMuPDF4LLM ⚡", mode="auto")
    md = layout_engine.apply_plan(raw, page, profile, plan) or raw
    return profile, plan, raw, md


def profile_str(p) -> str:
    return (
        f"col={p.columns} ovl={int(p.columns_overlap)} tab={int(p.has_tables)}"
        f"(fw={p.full_width_tables}) sm={int(p.has_small_text)} "
        f"ref={int(p.has_references)} idx={int(p.has_index)} body={p.body_blocks}"
    )


def check_boxes_in_md(page, md: str, issues: list[str], reorder: bool) -> int:
    """Check box content against the engine output.

    Content is never allowed to be lost. The "rendered as my table" check only
    applies when ``reorder_columns`` is in the plan (single-column pages leave
    boxes to the backend, e.g. pymupdf4llm renders them as headings/lists).
    """
    boxes = app._detect_boxes(page, page.rect.width, _table_regions(page))
    md_norm = normalize(md)
    n_ok = 0
    for bx in boxes:
        # Content presence (title or first content line must survive).
        sig = _norm(bx["title"] or "")
        if not sig:
            first = [
                _norm(ln.strip().strip("|").strip())
                for ln in bx["md"].splitlines()
                if ln.strip() and ln.strip() != "| --- |"
            ]
            sig = first[0] if first else ""
        if not sig:
            continue
        if normalize(sig) not in md_norm:
            issues.append(f"contenuto box perso: {sig[:30]!r}")
            continue
        if not reorder:
            n_ok += 1  # backend rendered it; content is present
            continue
        # reorder_columns ran: the box must be emitted as our table, once.
        if bx["md"] not in md:
            issues.append("box non reso come tabella")
            continue
        dup = any(
            md.count(_norm(ln.strip().strip("|").strip())) > 1
            for ln in bx["md"].splitlines()
            if len(_norm(ln.strip().strip("|").strip()).split()) >= 4
        )
        if dup:
            issues.append("testo box duplicato")
        else:
            n_ok += 1
    return n_ok


def survey(pdf_path: Path, pages: list[int]) -> dict:
    doc = pymupdf.open(str(pdf_path))
    totals = {"pages": 0, "crash": 0, "multi": 0, "order_ok": 0,
              "box_pages": 0, "boxes": 0, "box_ok": 0, "problems": 0}
    layout_kinds: dict[str, int] = {}
    for p in pages:
        page = doc[p - 1]
        totals["pages"] += 1
        try:
            profile, plan, raw, md = run_engine(page, pdf_path, p)
        except Exception as e:  # noqa: BLE001
            totals["crash"] += 1
            print(f"  !! p.{p}: ECCEZIONE {e!r}")
            continue

        plan_ids = ",".join(f.id for f in plan) or "-"
        key = f"col{profile.columns}"
        if profile.has_index:
            key += "+idx"
        if profile.has_references:
            key += "+ref"
        if profile.has_tables:
            key += "+tab"
        layout_kinds[key] = layout_kinds.get(key, 0) + 1

        issues: list[str] = []
        anchors = _geometry_anchors(page)
        order_ok = None
        if anchors:
            totals["multi"] += 1
            res = _check_order(md, anchors)
            order_ok = res["column_ok"]
            if order_ok:
                totals["order_ok"] += 1
            else:
                issues.append(f"ordine lettura errato (mancanti={res['missing']})")

        reorder = any(f.id == "reorder_columns" for f in plan)
        boxes = app._detect_boxes(page, page.rect.width, _table_regions(page))
        if boxes:
            totals["box_pages"] += 1
            totals["boxes"] += len(boxes)
            totals["box_ok"] += check_boxes_in_md(page, md, issues, reorder)

        if issues:
            totals["problems"] += 1

        order_s = ""
        if order_ok is not None:
            order_s = f"  ordine={'OK' if order_ok else 'KO'}"
        print(
            f"p.{p:>4d}  {profile_str(profile):55s}  piano=[{plan_ids}]{order_s}"
            + (f"  {issues}" if issues else "")
        )
    doc.close()
    print(f"  layout osservati: {layout_kinds}")
    return totals


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdfs", default="eco2022.pdf,david2027.pdf")
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--after", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

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
        doc.close()
        print(f"\n### {name} ({total} pagine) — campione {len(pages)}: {pages}\n")
        t = survey(pdf_path, pages)
        print(
            f"  riepilogo: crash={t['crash']} ordine={t['order_ok']}/{t['multi']} "
            f"box={t['boxes']}(ok={t['box_ok']}) pagine con problemi={t['problems']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
