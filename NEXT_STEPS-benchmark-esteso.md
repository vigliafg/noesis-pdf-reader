# Promemoria — benchmark esteso (da fare domani)

Redatto il 2026-08-17. **Promemoria per l'agente**: domani l'utente fornirà **5 PDF
nuovi**; testeremo **30 pagine casuali per ciascun PDF**, sia i 5 nuovi sia i 10 già
testati → **15 PDF × 30 = 450 pagine**.

## PDF già testati (in root — 30 pagine casuali ciascuno)

`andrew2020.pdf`, `cecil2024.pdf`, `david2027.pdf`, `eco2022.pdf`,
`harrison2025.pdf`, `mayo2020.pdf`, `oxford2020.pdf`, `robbins2021.pdf`,
`rosen2022.pdf`, `seidel2015.pdf`.

## Obiettivo

Passare da un tasso di successo *stimato* a uno *misurato*, estendendo il benchmark
oltre il solo ordine di lettura. Il contesto: a oggi l'engine adattativo misura
~97% sull'ordine di lettura (stima, campione piccolo) e ~90–95% sulla fedeltà
completa — entrambi da confermare con dati.

## Cosa misurare (non solo ordine)

1. **Ordine di lettura** (già coperto: ancore geometriche in `check_fixes.py`).
2. **Completezza** — il contenuto di box/tabelle/figura non va perso (oggi solo
   spottato; es. david p.763/p.1316, testo dentro box che pymupdf4llm non estrae).
3. **Duplicazioni** — nessun contenuto emesso due volte (box/tabelle sovrapposte).
4. **Crash / robustezza** su tutte le pagine campionate.

## Come procedere

- Riutilizzare `benchmark/check_engine.py` (profilo→piano→pipeline + ordine + box),
  estendendolo con un check di **completezza e duplicazione** (già in parte in
  `benchmark/check_boxes.py`).
- Se i PDF sono molto diversi tra loro, prima una scansione rapida
  (`benchmark/scan_layouts.py`) per capire i layout presenti, poi il campione.
- Campionare **30 pagine casuali per ciascuno dei 15 PDF** (5 nuovi + 10 già
  testati), con seed fisso per riproducibilità.
- Produrre un **report finale** col tasso misurato per asse (ordine / completezza /
  duplicazione / crash), confrontandolo con la stima ~97%/~90–95% data il
  2026-08-17.

## Note

- I PDF nuovi vanno messi nella root del progetto (come i 10 già presenti).
- Attenzione a `mayo2020.pdf`-like: se un PDF è un *estratto* (poche pagine),
  campionare comunque 30 pagine o dichiararlo.
- Aggiornare `VERIFICA_5_PDF.md` o creare un report nuovo (es.
  `RAPPORTO_BENCHMARK_5PDF.md`).

## Documenti collegati

- [RICERCA_OCR_STORICO.md](RICERCA_OCR_STORICO.md) — contesto OCR storico
- [VERIFICA_5_PDF.md](VERIFICA_5_PDF.md) — verifica precedente su 5 PDF
- [benchmark/check_engine.py](benchmark/check_engine.py) — tool da estendere
