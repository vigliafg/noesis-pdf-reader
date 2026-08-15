#!/usr/bin/env python3
"""Run the Docling engine benchmark on fixed pages of harrison2025.pdf.

Usage (from project root):
    .venv/bin/python -m benchmark.run_benchmark --engines A,B,C
    .venv/bin/python -m benchmark.run_benchmark --pages 1007,1009,1013,1015,1018,1019,1022
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark import engines, metrics  # noqa: E402

DEFAULT_PAGES = [1007, 1009, 1013, 1015, 1018, 1019, 1022]

ENGINES = {
    "A": ("docling low-overhead (pdfextractor)", engines.extract_docling_low_overhead),
    "B": ("docling current (noesis)", engines.extract_docling_current),
    "C": ("granite-docling (VLM)", engines.extract_granite_docling),
}


def _reference_md(pdf_path: Path, page: int) -> str:
    import pymupdf4llm

    return pymupdf4llm.to_markdown(str(pdf_path), pages=[page - 1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", default="harrison2025.pdf")
    ap.add_argument(
        "--pages",
        default=",".join(str(p) for p in DEFAULT_PAGES),
        help="1-based page numbers, comma-separated",
    )
    ap.add_argument("--engines", default="A,B,C", help="comma-separated: A,B,C")
    ap.add_argument("--out", default="benchmark/out")
    ap.add_argument("--force", action="store_true", help="re-run even if output exists")
    ap.add_argument(
        "--sample-pdf",
        default=None,
        help="optional small PDF to also run engine A on in whole-document mode",
    )
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDF non trovato: {pdf_path}", file=sys.stderr)
        return 1

    pages = [int(p) for p in args.pages.split(",") if p.strip()]
    engine_keys = [k.strip().upper() for k in args.engines.split(",") if k.strip()]
    for k in engine_keys:
        if k not in ENGINES:
            print(f"Motore sconosciuto: {k}", file=sys.stderr)
            return 1

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    report_path = out_root / "report.json"
    report = {}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            report = {}
    report.setdefault("pdf", str(pdf_path))
    report.setdefault("pages", [])
    report.setdefault("engines", {})
    report.setdefault("runs", {})
    report.setdefault("metrics", {})
    report.setdefault("reference_files", {})
    for p in pages:
        if p not in report["pages"]:
            report["pages"].append(p)
    report["pages"].sort()
    for k in engine_keys:
        report["engines"][k] = ENGINES[k][0]

    # Reference markdown (pymupdf4llm) per page — fast, computed once.
    print("Generating reference (pymupdf4llm)...")
    ref_dir = out_root / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)
    refs: dict[int, str] = {}
    for page in pages:
        ref_file = ref_dir / f"page_{page:04d}.md"
        if ref_file.exists() and not args.force:
            refs[page] = ref_file.read_text(encoding="utf-8")
        else:
            refs[page] = _reference_md(pdf_path, page)
            ref_file.write_text(refs[page], encoding="utf-8")
        report["reference_files"][str(page)] = str(ref_file)

    # Run each engine.
    for key in engine_keys:
        label, fn = ENGINES[key]
        print(f"\n=== Engine {key}: {label} ===")
        eng_dir = out_root / key
        eng_dir.mkdir(parents=True, exist_ok=True)
        runs = []
        for i, page in enumerate(pages):
            out_file = eng_dir / f"page_{page:04d}.md"
            if out_file.exists() and not args.force:
                md = out_file.read_text(encoding="utf-8")
                secs = None  # reused cached output, not timed
                print(f"  page {page}: cached")
            else:
                t0 = time.perf_counter()
                md = fn(pdf_path, page)
                secs = time.perf_counter() - t0
                out_file.write_text(md, encoding="utf-8")
                print(f"  page {page}: {secs:.1f}s ({len(md)} chars)")
            runs.append(
                {
                    "page": page,
                    "cold": i == 0,
                    "seconds": round(secs, 3) if secs is not None else None,
                    "chars": len(md),
                    "file": str(out_file),
                }
            )
        # Upsert per-page (repeated invocations accumulate instead of replacing).
        existing = {r["page"]: r for r in report["runs"].get(key, [])}
        for r in runs:
            existing[r["page"]] = r
        report["runs"][key] = [existing[p] for p in sorted(existing)]

        # Metrics against the reference.
        report["metrics"].setdefault(key, {})
        for page in pages:
            md = (eng_dir / f"page_{page:04d}.md").read_text(encoding="utf-8")
            report["metrics"][key][str(page)] = metrics.evaluate_page(
                md, refs[page], page
            )

    # Optional: engine A on a whole small document in one pass.
    if args.sample_pdf:
        sample = Path(args.sample_pdf)
        if sample.exists():
            print(f"\n=== Engine A whole-document on {sample} ===")
            t0 = time.perf_counter()
            md = engines.extract_docling_low_overhead_document(sample)
            secs = time.perf_counter() - t0
            (out_root / "A" / "sample_whole.md").write_text(md, encoding="utf-8")
            report["sample_doc"] = {
                "file": str(sample),
                "seconds": round(secs, 3),
                "chars": len(md),
            }
            print(f"  {secs:.1f}s ({len(md)} chars)")

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
