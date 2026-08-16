#!/usr/bin/env python3
"""Pre-generate GLM-OCR gold references for the reliability benchmark.

Usage (from project root)::

    .venv/bin/python -m benchmark.gen_gold --pages 156,157,1007 --dpi 200
    .venv/bin/python -m benchmark.gen_gold --pages 157 --dpi 200 --out /tmp/gold200
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark import arbiter  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", default="harrison2025.pdf")
    ap.add_argument("--pages", required=True, help="1-based page numbers, comma-separated")
    ap.add_argument("--dpi", type=int, default=150, help="render DPI for GLM-OCR")
    ap.add_argument("--out", default="benchmark/out/reliability/gold")
    ap.add_argument("--force", action="store_true", help="re-generate even if cached")
    args = ap.parse_args()

    pages = [int(p) for p in args.pages.split(",") if p.strip()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for p in pages:
        f = out / f"page_{p:04d}.md"
        if f.exists() and not args.force:
            print(f"page {p}: cached", flush=True)
            continue
        print(f"page {p}: GLM-OCR dpi={args.dpi} ...", flush=True)
        t0 = time.perf_counter()
        md = arbiter.extract_glm_ocr(args.pdf, p, dpi=args.dpi)
        f.write_text(md, encoding="utf-8")
        print(f"  -> {time.perf_counter() - t0:.1f}s {len(md)} chars", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
