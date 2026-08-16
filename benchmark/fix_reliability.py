#!/usr/bin/env python3
"""Reliability benchmark: internal engines (with/without the column fix)
measured against an AI OCR gold reference (GLM-OCR via Ollama).

Usage (from project root)::

    .venv/bin/python -m benchmark.fix_reliability --pages 157
    .venv/bin/python -m benchmark.fix_reliability --pages 157,156,1007

The "gold" is GLM-OCR transcribing the rendered page image. Each candidate is
scored with chrF / word-F1 (content fidelity) plus a paragraph-count parity and
a reading-order score against the gold.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark import arbiter, metrics  # noqa: E402

DEFAULT_PAGES = [157]

# candidate approaches: key -> (label, extractor(pdf_path, page_1based) -> str)
def _candidate_registry() -> dict[str, tuple[str, object]]:
    import pymupdf4llm  # noqa: F401

    import main

    def _extract_pymupdf4llm(pdf_path: Path, page: int) -> str:
        import pymupdf4llm
        return pymupdf4llm.to_markdown(str(pdf_path), pages=[page - 1])

    def _extract_column_fix(pdf_path: Path, page: int) -> str:
        import pymupdf
        doc = pymupdf.open(str(pdf_path))
        try:
            return main._column_aware_markdown(doc[page - 1], move_title=False)
        finally:
            doc.close()

    def _extract_docling_raw(pdf_path: Path, page: int) -> str:
        from benchmark import engines
        return engines.extract_docling_current(pdf_path, page)

    def _extract_docling(pdf_path: Path, page: int) -> str:
        # main.py's actual Docling path: de-hyphenation + cross-column re-split.
        import pymupdf
        from benchmark import engines
        md = engines.extract_docling_current(pdf_path, page)
        md = main.clean_text(md).strip()
        doc = pymupdf.open(str(pdf_path))
        try:
            return main._split_cross_column_paragraphs(md, doc[page - 1])
        finally:
            doc.close()

    return {
        "pymupdf4llm": ("pymupdf4llm (senza fix)", _extract_pymupdf4llm),
        "colonne-fix": ("fix euristico colonne (con fix)", _extract_column_fix),
        "docling": ("docling + fix incollatura (con fix)", _extract_docling),
        "docling-raw": ("docling raw (senza fix)", _extract_docling_raw),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", default="harrison2025.pdf")
    ap.add_argument(
        "--pages",
        default=",".join(str(p) for p in DEFAULT_PAGES),
        help="1-based page numbers, comma-separated",
    )
    ap.add_argument("--dpi", type=int, default=150, help="render DPI for GLM-OCR")
    ap.add_argument("--out", default="benchmark/out/reliability")
    ap.add_argument(
        "--candidates",
        default="pymupdf4llm,colonne-fix,docling,docling-raw",
        help="comma-separated candidate keys",
    )
    ap.add_argument("--force", action="store_true", help="re-run even if cached")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDF non trovato: {pdf_path}", file=sys.stderr)
        return 1

    pages = [int(p) for p in args.pages.split(",") if p.strip()]
    registry = _candidate_registry()
    cand_keys = [k.strip() for k in args.candidates.split(",") if k.strip()]
    for k in cand_keys:
        if k not in registry:
            print(f"Candidato sconosciuto: {k} (disponibili: {', '.join(registry)})",
                  file=sys.stderr)
            return 1

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    gold_dir = out_root / "gold"
    gold_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "pdf": str(pdf_path),
        "pages": pages,
        "gold": {"model": arbiter.MODEL, "dpi": args.dpi, "url": arbiter.OLLAMA_URL},
        "candidates": {k: registry[k][0] for k in cand_keys},
        "metrics": {},
        "files": {},
    }

    # 1. Gold references (GLM-OCR), cached per page.
    for page in pages:
        gf = gold_dir / f"page_{page:04d}.md"
        if gf.exists() and not args.force:
            print(f"gold page {page}: cached")
        else:
            print(f"gold page {page}: GLM-OCR (dpi={args.dpi})...")
            t0 = time.perf_counter()
            md = arbiter.extract_glm_ocr(pdf_path, page, dpi=args.dpi)
            secs = time.perf_counter() - t0
            gf.write_text(md, encoding="utf-8")
            print(f"  -> {secs:.1f}s, {len(md)} chars")
        report["files"].setdefault("gold", {})[str(page)] = str(gf)

    # 2. Candidates, cached per page.
    cand_dir: dict[str, Path] = {}
    for key in cand_keys:
        d = out_root / key
        d.mkdir(parents=True, exist_ok=True)
        cand_dir[key] = d
        label, fn = registry[key]
        for page in pages:
            cf = d / f"page_{page:04d}.md"
            if cf.exists() and not args.force:
                print(f"  [{key}] page {page}: cached")
                continue
            print(f"  [{key}] page {page}: {label}...")
            t0 = time.perf_counter()
            try:
                md = fn(pdf_path, page)
            except Exception as e:  # noqa: BLE001
                md = f"(errore: {e})"
            secs = time.perf_counter() - t0
            cf.write_text(md, encoding="utf-8")
            print(f"    -> {secs:.1f}s, {len(md)} chars")
        report["files"].setdefault(key, {})[str(page)] = str(cand_dir[key] / f"page_{page:04d}.md")

    # 3. Metrics vs gold.
    for key in cand_keys:
        report["metrics"][key] = {}
        for page in pages:
            gold_md = (gold_dir / f"page_{page:04d}.md").read_text(encoding="utf-8")
            cand_md = (cand_dir[key] / f"page_{page:04d}.md").read_text(encoding="utf-8")
            report["metrics"][key][str(page)] = metrics.evaluate_reliability(
                cand_md, gold_md, page
            )

    # 4. Write JSON + a markdown summary.
    rj = out_root / "report.json"
    rj.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md = _build_markdown(report)
    (out_root / "report.md").write_text(md, encoding="utf-8")
    print("\n" + md)
    print(f"\nReport scritto in {rj} e {out_root / 'report.md'}")
    return 0


def _avg(rows: list[float]) -> float:
    return round(sum(rows) / len(rows), 4) if rows else 0.0


def _build_markdown(report: dict) -> str:
    pages = report["pages"]
    lines: list[str] = []
    lines.append("# Benchmark affidabilità fix colonne — GLM-OCR come gold\n")
    lines.append(
        f"Gold: **{report['gold']['model']}** (Ollama, {report['gold']['dpi']} DPI). "
        f"Pagine: {', '.join(str(p) for p in pages)} di `{Path(report['pdf']).name}`.\n"
    )
    lines.append("| candidato | chrF medio | word-F1 medio | ancore in ordine | ancore mancanti |")
    lines.append("|---|---|---|---|---|")
    # Gold row (its own reliability, measured against the human-curated anchors).
    gold_in_order = gold_missing = 0
    for p in pages:
        gmd = Path(report["files"]["gold"][str(p)]).read_text(encoding="utf-8")
        ga = metrics.anchor_order_check(gmd, p)
        gold_in_order += ga["in_order"]
        gold_missing += len(ga["missing"])
    lines.append(
        f"| gold — {report['gold']['model']} ({report['gold']['dpi']} DPI) "
        f"| — | — | {gold_in_order}/{len(pages)} | {gold_missing} |"
    )
    for key, label in report["candidates"].items():
        m = report["metrics"][key]
        chrf = _avg([m[str(p)]["chrf"] for p in pages])
        wf1 = _avg([m[str(p)]["word_f1"] for p in pages])
        in_order = sum(1 for p in pages if m[str(p)]["anchors"]["in_order"])
        missing = sum(len(m[str(p)]["anchors"]["missing"]) for p in pages)
        lines.append(f"| {key} — {label} | {chrf} | {wf1} | {in_order}/{len(pages)} | {missing} |")
    lines.append("")

    lines.append("Dettaglio per pagina (chrF / word-F1):\n")
    lines.append("| pagina | " + " | ".join(report["candidates"].keys()) + " |")
    lines.append("|---|" + "---|" * len(report["candidates"]))
    for p in pages:
        cells = []
        for key in report["candidates"]:
            m = report["metrics"][key][str(p)]
            cells.append(f"{m['chrf']} / {m['word_f1']}")
        lines.append(f"| {p} | " + " | ".join(cells) + " |")
    lines.append("")

    # key strings (presence), gold first.
    for p in pages:
        frags = report["metrics"][next(iter(report["candidates"]))][str(p)]["key_strings"]
        if not frags:
            continue
        lines.append(f"Stringhe-chiave pagina {p} (presenti?):\n")
        lines.append("| frammento | gold | " + " | ".join(report["candidates"].keys()) + " |")
        lines.append("|---|---|" + "---|" * len(report["candidates"]))
        gold_md = (Path(report["files"]["gold"][str(p)])).read_text(encoding="utf-8")
        for frag in frags:
            gold_present = metrics.check_key_strings(gold_md, p).get(frag, False)
            cells = [str(gold_present)]
            for key in report["candidates"]:
                cells.append(str(report["metrics"][key][str(p)]["key_strings"].get(frag, False)))
            lines.append(f"| {frag} | " + " | ".join(cells) + " |")
        lines.append("")

    # Reading-order anchors (human-curated true order).
    for p in pages:
        anchors = metrics.READING_ORDER_ANCHORS.get(p, [])
        if not anchors:
            continue
        lines.append(f"Ancore ordine di lettura pagina {p} (posizione nel testo):\n")
        lines.append("| # | ancora | gold | " + " | ".join(report["candidates"].keys()) + " |")
        lines.append("|---|---|" + "---|" * (len(report["candidates"]) + 1))
        gold_md = Path(report["files"]["gold"][str(p)]).read_text(encoding="utf-8")
        gold_aligned = metrics.anchor_order_check(gold_md, p)["aligned"]
        for i, frag in enumerate(anchors):
            cells = [str(gold_aligned[i]) if gold_aligned[i] is not None else "—"]
            for key in report["candidates"]:
                a = report["metrics"][key][str(p)]["anchors"]
                pos = a["aligned"][i]
                cells.append(str(pos) if pos is not None else "—")
            lines.append(f"| {i} | {frag} | " + " | ".join(cells) + " |")
        lines.append("")
        lines.append("Esito `in_order` (posizioni non decrescenti):\n")
        lines.append("| gold | " + " | ".join(report["candidates"].keys()) + " |")
        lines.append("|---|" + "---|" * len(report["candidates"]))
        g = metrics.anchor_order_check(gold_md, p)
        cells = [f"{g['in_order']} (mancanti: {len(g['missing'])})"]
        for key in report["candidates"]:
            a = report["metrics"][key][str(p)]["anchors"]
            cells.append(f"{a['in_order']} (mancanti: {len(a['missing'])})")
        lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
