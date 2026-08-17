#!/usr/bin/env python3
"""Survey dei layout: coprono i fix esistenti i nuovi PDF?

Per ogni PDF, campiona ``--pages`` pagine casuali (da ``--after`` in poi, seed
fisso) e per ciascuna:

1. calcola i *segnali di profilo* (colonne, tabelle, font piccoli, referenze,
   indice) — la stessa famiglia di segnali che usera' il ``LayoutProfile``
   dell'engine adattativo;
2. estrae la pagina con pymupdf4llm (raw) e con la pipeline fix esistente
   (``_column_aware_markdown``);
3. se la pagina e' a piu' colonne, verifica l'ordine di lettura con le ancore
   geometriche (stessa logica di ``check_fixes.py``).

L'obiettivo e' capire se i 5 fix esistenti coprono i layout dei nuovi PDF, o
se servono fix/signali nuovi. Solo backend veloci (pymupdf4llm), niente docling.

Usage (dalla root del progetto)::

    .venv/bin/python -m benchmark.survey_layouts \
        --pdfs rosen2022.pdf,andrew2020.pdf --pages 10 --after 200 --seed 7
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf  # noqa: E402
import pymupdf4llm  # noqa: E402

import main as app  # noqa: E402
from benchmark.check_fixes import _check_order, _geometry_anchors  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
#  profilo (segnali del layout) — preview del LayoutProfile dell'engine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LayoutProfile:
    columns: int
    splits: tuple[float, ...]
    columns_overlap: bool
    has_tables: bool
    full_width_tables: int
    has_small_text: bool
    has_references: bool
    has_index: bool
    body_blocks: int


def _data_tables(page) -> list:
    """Data tables (as the fixes see them): exclude 1x1/1x2 chapter-title blocks."""
    try:
        tabs = page.find_tables()
    except Exception:
        return []
    out = []
    for t in tabs.tables:
        if t.row_count <= 1 and t.col_count <= 2:
            continue
        out.append(t)
    return out


def _body_blocks(page) -> tuple[list, list]:
    """Return (table_regions, body_blocks) mirroring _column_aware_markdown."""
    table_regions: list[tuple] = []
    for t in _data_tables(page):
        table_regions.append(tuple(t.bbox))

    def _inside(b: dict, r: tuple) -> bool:
        return (
            b["x0"] >= r[0] - 2 and b["x1"] <= r[2] + 2
            and b["y0"] >= r[1] - 2 and b["y1"] <= r[3] + 2
        )

    blocks = [b for b in app._collect_blocks(page) if b["max_size"] >= 6.5]
    body = [
        b for b in blocks
        if not any(_inside(b, r) for r in table_regions)
        and (b["x1"] - b["x0"]) < 0.6 * page.rect.width
        and (b["x1"] - b["x0"]) >= 25
    ]
    return table_regions, body


def profile_page(page) -> LayoutProfile:
    """Compute the layout signals for one page (pure, pymupdf only)."""
    pw = page.rect.width
    table_regions, body = _body_blocks(page)
    splits = tuple(app._detect_column_splits(body, pw))
    n_cols = len(splits) + 1

    # columns_overlap: first and last column share vertical space (side by side).
    columns_overlap = False
    if n_cols >= 2:
        def _col(b: dict) -> int:
            return sum(1 for s in splits if (b["x0"] + b["x1"]) / 2 > s)
        cols: list[list[dict]] = [[] for _ in range(n_cols)]
        for b in body:
            cols[_col(b)].append(b)
        cols = [c for c in cols if c]
        if len(cols) >= 2:
            first, last = cols[0], cols[-1]
            columns_overlap = any(
                lb["y0"] <= rb["y1"] and rb["y0"] <= lb["y1"]
                for lb in first for rb in last
            )

    tables = _data_tables(page)
    has_tables = bool(tables)
    full_width_tables = sum(1 for t in tables if (t.bbox[2] - t.bbox[0]) >= 0.6 * pw)

    has_small_text = any(b["max_size"] < 7.5 for b in body)

    # has_references: in >=2 colonne, la maggior parte dei blocchi inizia con
    # "1." e c'e' testo piccolo.
    has_references = False
    if n_cols >= 2 and has_small_text and body:
        numbered = sum(1 for b in body if re.match(r"^\s*\d+\.", _block_text(b)))
        has_references = numbered >= 0.5 * len(body)

    # has_index: >=3 colonne, righe corte con suffissi numerici (", 1787").
    has_index = False
    if n_cols >= 3 and body:
        suffix = sum(
            1 for b in body
            if re.search(r"\d{3,}[a-z]?$", _block_text(b).strip()) and _block_text(b).count(" ") <= 12
        )
        has_index = suffix >= 0.4 * len(body)

    return LayoutProfile(
        columns=n_cols,
        splits=splits,
        columns_overlap=columns_overlap,
        has_tables=has_tables,
        full_width_tables=full_width_tables,
        has_small_text=has_small_text,
        has_references=has_references,
        has_index=has_index,
        body_blocks=len(body),
    )


def _block_text(b: dict) -> str:
    return " ".join(s["text"] for line in b["lines"] for s in line)


# ─────────────────────────────────────────────────────────────────────────────
#  survey
# ─────────────────────────────────────────────────────────────────────────────

def _profile_str(p: LayoutProfile) -> str:
    return (
        f"col={p.columns}({','.join(f'{s:.0f}' for s in p.splits)}) "
        f"ovl={int(p.columns_overlap)} tab={int(p.has_tables)}"
        f"(fw={p.full_width_tables}) sm={int(p.has_small_text)} "
        f"ref={int(p.has_references)} idx={int(p.has_index)} body={p.body_blocks}"
    )


def survey_pdf(pdf_path: Path, pages_1based: list[int]) -> dict:
    doc = pymupdf.open(str(pdf_path))
    rows = []
    for p in pages_1based:
        page = doc[p - 1]
        prof = profile_page(page)
        t0 = time.perf_counter()
        raw = pymupdf4llm.to_markdown(str(pdf_path), pages=[p - 1])
        fixed = app._column_aware_markdown(page)
        secs = time.perf_counter() - t0

        order = None
        if prof.columns >= 2:
            anchors = _geometry_anchors(page)
            if anchors:
                order = {
                    "raw": _check_order(raw, anchors),
                    "fix": _check_order(fixed, anchors),
                }

        rows.append(
            {
                "page": p,
                "profile": prof,
                "raw_chars": len(raw),
                "fix_chars": len(fixed),
                "order": order,
                "secs": secs,
            }
        )
    doc.close()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdfs", default="rosen2022.pdf,andrew2020.pdf,robbins2021.pdf,seidel2015.pdf,mayo2020.pdf")
    ap.add_argument("--pages", type=int, default=10, help="pagine campione per PDF")
    ap.add_argument("--after", type=int, default=200, help="campiona solo pagine > questo (1-based)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    total_pages = 0
    multi_col = 0
    checked = 0
    fix_ok = 0
    raw_ok = 0
    layout_counts: dict[str, int] = {}

    for name in args.pdfs.split(","):
        pdf_path = Path(name.strip())
        if not pdf_path.exists():
            print(f"PDF non trovato: {pdf_path}", file=sys.stderr)
            continue
        doc = pymupdf.open(str(pdf_path))
        total = doc.page_count
        doc.close()
        pool = list(range(max(args.after, 0), total))
        if not pool:
            print(f"{name}: nessuna pagina >= {args.after} ({total} totali) — campiono da p.1")
            pool = list(range(total))
        random.seed(args.seed)
        pages = sorted(random.sample(pool, min(args.pages, len(pool))))

        print(f"\n### {name} ({total} pagine) — campione: {pages}\n")
        rows = survey_pdf(pdf_path, pages)
        for r in rows:
            total_pages += 1
            prof = r["profile"]
            key = f"col{prof.columns}"
            if prof.has_index:
                key += "+idx"
            if prof.has_references:
                key += "+ref"
            if prof.has_tables:
                key += "+tab"
            layout_counts[key] = layout_counts.get(key, 0) + 1

            order_s = ""
            if r["order"]:
                multi_col += 1
                checked += 1
                o = r["order"]
                raw_ok += int(o["raw"]["column_ok"])
                fix_ok += int(o["fix"]["column_ok"])
                order_s = (
                    f"  raw_col_ok={o['raw']['column_ok']!s:5s} "
                    f"fix_col_ok={o['fix']['column_ok']!s:5s} "
                    f"mancanti_raw={o['raw']['missing']} mancanti_fix={o['fix']['missing']}"
                )
            print(
                f"p.{r['page']:>4d}  {_profile_str(prof):62s} "
                f"raw={r['raw_chars']:>6d} fix={r['fix_chars']:>6d} ({r['secs']:.2f}s){order_s}"
            )
        print()

    print("=" * 78)
    print("RIEPILOGO")
    print(f"pagine totali: {total_pages}")
    print(f"layout osservati: {layout_counts}")
    print(f"pagine a multi-colonna verificate: {checked}")
    if checked:
        print(f"ordine lettura corretto — raw: {raw_ok}/{checked}   fix: {fix_ok}/{checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
