"""Image-extraction comparison: Docling PictureItem (A) vs pymupdf XObjects (current)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark import engines  # noqa: E402

DEFAULT_PAGES = [1007, 1009, 1013, 1015, 1018, 1019, 1022]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", default="harrison2025.pdf")
    ap.add_argument(
        "--pages",
        default=",".join(str(p) for p in DEFAULT_PAGES),
        help="1-based page numbers, comma-separated",
    )
    ap.add_argument("--out", default="benchmark/out/images")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDF non trovato: {pdf_path}", file=sys.stderr)
        return 1

    pages = [int(p) for p in args.pages.split(",") if p.strip()]
    out_root = Path(args.out)
    report: dict[str, dict] = {}

    for page in pages:
        print(f"page {page}:")
        docling_dir = out_root / "docling_pictureitem" / f"page_{page:04d}"
        pymupdf_dir = out_root / "pymupdf_xobject" / f"page_{page:04d}"

        t0 = __import__("time").perf_counter()
        docling_imgs = engines.extract_images_docling(pdf_path, page, docling_dir)
        docling_secs = __import__("time").perf_counter() - t0

        t0 = __import__("time").perf_counter()
        pymupdf_imgs = engines.extract_images_pymupdf(pdf_path, page, pymupdf_dir)
        pymupdf_secs = __import__("time").perf_counter() - t0

        print(f"  docling PictureItem : {len(docling_imgs)} images ({docling_secs:.1f}s)")
        print(f"  pymupdf XObject     : {len(pymupdf_imgs)} images ({pymupdf_secs:.1f}s)")

        report[str(page)] = {
            "docling_pictureitem": {
                "count": len(docling_imgs),
                "seconds": round(docling_secs, 3),
                "files": docling_imgs,
            },
            "pymupdf_xobject": {
                "count": len(pymupdf_imgs),
                "seconds": round(pymupdf_secs, 3),
                "files": pymupdf_imgs,
            },
        }

    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / "report.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
