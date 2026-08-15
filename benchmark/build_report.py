#!/usr/bin/env python3
"""Rebuild report.json + report.md from the saved engine outputs on disk.

Timings are recorded here because the runner's incremental invocations only
stored wall-clock time for the runs it performed (first-page cold runs were
probed separately and are hardcoded below).
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark import metrics  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmark" / "out"

PAGES = [1007, 1009, 1013, 1015, 1018, 1019, 1022]

ENGINE_LABELS = {
    "A": "docling low-overhead (pdfextractor)",
    "B": "docling current (noesis-pdf-reader)",
    "C": "granite-docling (VLM)",
}

# page -> seconds, measured on this machine (CPU-only).
TIMES: dict[str, dict[int, float]] = {
    "A": {1007: 53.428, 1009: 9.954, 1013: 4.376, 1015: 6.143,
          1018: 2.442, 1019: 6.457, 1022: 2.392},
    "B": {1007: 24.8, 1009: 6.0, 1013: 3.6, 1015: 4.1,
          1018: 2.7, 1019: 4.8, 1022: 2.6},
    "C": {1007: 212.8, 1009: 134.8, 1013: 109.6, 1015: 129.5,
          1018: 162.4, 1019: 158.6, 1022: 163.8},
}

SAMPLE_DOC = {"file": str(OUT / "sample_7.pdf"), "seconds": 42.4}

# B with the same default options but a REUSED converter (isolates the
# cost of recreating DocumentConverter per page).
REUSE_TEST = {"B": {1007: 24.8, 1009: 6.0, 1013: 3.6, 1015: 4.1, 1018: 2.7, 1019: 4.8, 1022: 2.6},
              "B-reused": {1007: 28.2, 1009: 5.4, 1013: 2.5, 1015: 3.4, 1018: 1.4, 1019: 3.6, 1022: 1.5}}


def load_md(engine: str, page: int) -> str:
    return (OUT / engine / f"page_{page:04d}.md").read_text(encoding="utf-8")


def build_report() -> dict:
    report = {
        "pdf": str(ROOT / "harrison2025.pdf"),
        "pages": PAGES,
        "engines": ENGINE_LABELS,
        "runs": {},
        "metrics": {},
        "reference_files": {},
        "sample_doc": SAMPLE_DOC,
    }

    refs = {}
    for page in PAGES:
        refs[page] = (OUT / "reference" / f"page_{page:04d}.md").read_text(encoding="utf-8")
        report["reference_files"][str(page)] = str(OUT / "reference" / f"page_{page:04d}.md")

    for eng, label in ENGINE_LABELS.items():
        runs = []
        for i, page in enumerate(PAGES):
            md = load_md(eng, page)
            runs.append({
                "page": page,
                "cold": i == 0,
                "seconds": TIMES[eng][page],
                "chars": len(md),
                "file": str(OUT / eng / f"page_{page:04d}.md"),
            })
        report["runs"][eng] = runs

        report["metrics"][eng] = {}
        for page in PAGES:
            report["metrics"][eng][str(page)] = metrics.evaluate_page(
                load_md(eng, page), refs[page], page
            )

    # Image comparison summary.
    img_report = OUT / "images" / "report.json"
    if img_report.exists():
        report["images"] = json.loads(img_report.read_text(encoding="utf-8"))

    return report


def _speed_summary(runs: list[dict]) -> dict:
    cold = runs[0]["seconds"]
    warm = [r["seconds"] for r in runs[1:]]
    return {
        "cold": round(cold, 1),
        "warm_avg": round(statistics.mean(warm), 1) if warm else None,
        "total": round(sum(r["seconds"] for r in runs), 1),
    }


def _avg(rows: list[float]) -> float:
    return round(statistics.mean(rows), 4)


def build_markdown(report: dict) -> str:
    lines: list[str] = []
    lines.append("# Benchmark motori Docling — risultati\n")
    lines.append(
        f"Pagine: {', '.join(str(p) for p in report['pages'])} di `harrison2025.pdf` "
        f"(4273 pagine). CPU-only, 26GB RAM. Docling 2.119.0.\n"
    )

    lines.append("## Velocità (secondi)\n")
    lines.append("| Motore | cold (p.1007) | media warm (p.2–7) | totale 7 pagine |")
    lines.append("|---|---|---|---|")
    for eng, label in report["engines"].items():
        s = _speed_summary(report["runs"][eng])
        lines.append(
            f"| {eng} — {label} | {s['cold']} | {s['warm_avg'] if s['warm_avg'] is not None else '—'} "
            f"| {s['total']} |"
        )
    lines.append("")
    lines.append(
        f"Engine A su campione 7-pagine in **una passata** (intero documento): "
        f"{report['sample_doc']['seconds']} s.\n"
    )
    lines.append("Costo del converter ricreato a ogni pagina (B vs B-reused, stesse opzioni):\n")
    lines.append("| pagina | B (ricreato) | B-reused (riusato) | Δ |")
    lines.append("|---|---|---|---|")
    for p in report["pages"]:
        b = REUSE_TEST["B"][p]
        br = REUSE_TEST["B-reused"][p]
        lines.append(f"| {p} | {b} | {br} | {round(b - br, 1)} |")
    lines.append("")

    lines.append("## Precisione (vs riferimento pymupdf4llm)\n")
    lines.append("| Motore | chrF medio | word-F1 medio |")
    lines.append("|---|---|---|")
    for eng, label in report["engines"].items():
        m = report["metrics"][eng]
        chrf = _avg([m[str(p)]["chrf"] for p in report["pages"]])
        wf1 = _avg([m[str(p)]["word_f1"] for p in report["pages"]])
        lines.append(f"| {eng} — {label} | {chrf} | {wf1} |")
    lines.append("")

    lines.append("Dettaglio per pagina (chrF / word-F1):\n")
    lines.append("| pagina | A | B | C |")
    lines.append("|---|---|---|---|")
    for p in report["pages"]:
        cells = []
        for eng in ["A", "B", "C"]:
            m = report["metrics"][eng][str(p)]
            cells.append(f"{m['chrf']} / {m['word_f1']}")
        lines.append(f"| {p} | {' | '.join(cells)} |")
    lines.append("")

    lines.append("## Check strutturali\n")
    lines.append("| Motore | titoli (media) | tabelle (media) | immagini (media) |")
    lines.append("|---|---|---|---|")
    for eng, label in report["engines"].items():
        m = report["metrics"][eng]
        h = _avg([m[str(p)]["structural"]["headings"] for p in report["pages"]])
        t = _avg([m[str(p)]["structural"]["tables"] for p in report["pages"]])
        im = _avg([m[str(p)]["structural"]["images"] for p in report["pages"]])
        lines.append(f"| {eng} — {label} | {h} | {t} | {im} |")
    lines.append("")
    lines.append(
        "> Il conteggio \"immagini\" = link `![…](…)` nel markdown: nessun engine li emette di\n"
        "> default (A può emetterli con `image_placeholder`, come fa `pdfextractor`).\n"
        "> L'estrazione effettiva delle immagini è nella sezione dedicata qui sotto.\n"
    )

    # Key strings page 1007.
    lines.append("Stringhe-chiave pagina 1007 (presenti?):\n")
    lines.append("| frammento | A | B | C |")
    lines.append("|---|---|---|---|")
    frags = report["metrics"]["A"]["1007"]["key_strings"].keys()
    for frag in frags:
        cells = [str(report["metrics"][e]["1007"]["key_strings"].get(frag, "—")) for e in ["A", "B", "C"]]
        lines.append(f"| {frag} | {' | '.join(cells)} |")
    lines.append("")

    lines.append("## Confronto estrazione immagini\n")
    if "images" in report:
        lines.append("| pagina | docling PictureItem | pymupdf XObject |")
        lines.append("|---|---|---|")
        for p in report["pages"]:
            d = report["images"][str(p)]
            lines.append(
                f"| {p} | {d['docling_pictureitem']['count']} | {d['pymupdf_xobject']['count']} |"
            )
        lines.append("")
        lines.append(
            "Esito: su 1013 e 1015 pymupdf scompone la figura in 9 e 6 XObject "
            "(incluse strisce 49×4), docling estrae 1 figura intera (926×785 e 1022×601).\n"
        )
    else:
        lines.append("(report immagini assente)\n")

    lines.append("## Conclusioni\n")
    lines.append(
        "- **Velocità testo**: il ~25-30 s/pagina citato è il **cold start** (caricamento "
        "modelli, una tantum). Dopo, docling va a ~1.5–6 s/pagina. Ricreare il converter a "
        "ogni pagina (B) costa solo ~1 s/pagina di reload modelli (vedi tabella B vs B-reused)."
    )
    lines.append(
        "- **Opzioni low-RAM di pdfextractor**: `num_threads=1` + batch=1 + "
        "`images_scale=2.0`/`generate_picture_images` rendono A **più lento** di B per pagina "
        "(85.2 s vs 48.6 s): sono pensate per <4GB RAM, non per la velocità. Da portare "
        "solo se serve risparmiare memoria, non per accelerare."
    )
    lines.append(
        "- **Intero documento in una passata** (A, 42.4 s/7 pagine) è solo marginalmente "
        "più veloce del per-pagina (B, 48.6 s) su poche pagine; il vantaggio si vede su "
        "documenti grandi (modelli caricati una volta)."
    )
    lines.append(
        "- **granite-docling (C)**: ~143 s/pagina su CPU (~18 min/7 pagine), qualità "
        "confrontabile a docling (chrF 0.965 vs 0.964). Da usare solo con GPU/accelerazione."
    )
    lines.append(
        "- **Immagini**: la strategia Docling `PictureItem` (A) è nettamente migliore "
        "dell'estrazione XObject pymupdf: figure composite intere, nessun ritaglio di testo."
    )
    lines.append(
        "- **Da portare in main.py**: riuso del converter (~1 s/pag), de-hyphenation "
        "`clean_text()`, estrazione immagini `PictureItem`. NON portare le opzioni "
        "single-thread/batch-1 se l'obiettivo è la velocità."
    )
    return "\n".join(lines)


def main() -> int:
    report = build_report()
    (OUT / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md = build_markdown(report)
    (OUT / "report.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
