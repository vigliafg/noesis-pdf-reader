#!/usr/bin/env python3
"""Scansione completa (veloce) dei layout dei PDF.

Versione senza ``find_tables`` (che domina il costo): conta per ogni PDF le
pagine per categoria di layout e riporta esempi per quelle "interessanti"
(colonne >= 3, indice, referenze, testo piccolo). Serve a capire se i libri
contengono layout che i fix esistenti non coprono.

Usage::

    .venv/bin/python -m benchmark.scan_layouts --pdfs rosen2022.pdf,andrew2020.pdf
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf  # noqa: E402

import main as app  # noqa: E402


def _block_text(b: dict) -> str:
    return " ".join(s["text"] for line in b["lines"] for s in line)


def fast_profile(page) -> dict:
    """Segnali di layout SENZA find_tables (veloce)."""
    pw = page.rect.width
    blocks = [b for b in app._collect_blocks(page) if b["max_size"] >= 6.5]
    body = [
        b for b in blocks
        if (b["x1"] - b["x0"]) < 0.6 * pw and (b["x1"] - b["x0"]) >= 25
    ]
    splits = app._detect_column_splits(body, pw)
    n_cols = len(splits) + 1
    has_small_text = any(b["max_size"] < 7.5 for b in body)

    has_references = False
    if n_cols >= 2 and has_small_text and body:
        numbered = sum(1 for b in body if re.match(r"^\s*\d+\.", _block_text(b)))
        has_references = numbered >= 0.5 * len(body)

    has_index = False
    if n_cols >= 3 and body:
        suffix = sum(
            1 for b in body
            if re.search(r"\d{3,}[a-z]?$", _block_text(b).strip())
            and _block_text(b).count(" ") <= 12
        )
        has_index = suffix >= 0.4 * len(body)

    return {
        "columns": n_cols,
        "has_small_text": has_small_text,
        "has_references": has_references,
        "has_index": has_index,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdfs", default="rosen2022.pdf,andrew2020.pdf,robbins2021.pdf,seidel2015.pdf,mayo2020.pdf")
    ap.add_argument("--max-pages", type=int, default=0, help="limita a N pagine per PDF (0 = tutte)")
    args = ap.parse_args()

    for name in args.pdfs.split(","):
        pdf_path = Path(name.strip())
        if not pdf_path.exists():
            print(f"PDF non trovato: {pdf_path}", file=sys.stderr)
            continue
        doc = pymupdf.open(str(pdf_path))
        total = doc.page_count
        limit = total if args.max_pages <= 0 else min(args.max_pages, total)
        counts = {"col1": 0, "col2": 0, "col3+": 0, "idx": 0, "ref": 0, "small": 0}
        examples = {"col3+": [], "idx": [], "ref": [], "small": []}
        t0 = time.perf_counter()
        for i in range(limit):
            try:
                p = fast_profile(doc[i])
            except Exception:
                continue
            c = p["columns"]
            if c >= 3:
                counts["col3+"] += 1
                if len(examples["col3+"]) < 8:
                    examples["col3+"].append(i + 1)
            elif c == 2:
                counts["col2"] += 1
            else:
                counts["col1"] += 1
            for key, flag in (("idx", "has_index"), ("ref", "has_references"), ("small", "has_small_text")):
                if p[flag]:
                    counts[key] += 1
                    if len(examples[key]) < 8:
                        examples[key].append(i + 1)
        doc.close()
        print(f"### {name} ({total} pagine, scan {limit}, {time.perf_counter()-t0:.0f}s)")
        print(f"  col1={counts['col1']}  col2={counts['col2']}  col3+={counts['col3+']}  es.={examples['col3+']}")
        print(f"  idx={counts['idx']} es.={examples['idx']}")
        print(f"  ref={counts['ref']} es.={examples['ref']}")
        print(f"  small={counts['small']} es.={examples['small']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
