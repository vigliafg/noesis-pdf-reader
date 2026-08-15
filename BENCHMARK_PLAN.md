# Piano — Benchmark 3 motori Docling + strategia estrazione immagini

Redatto il 2026-08-15. Leggere tutto prima di iniziare.

## Obiettivo

Confrontare, **sulle stesse 7 pagine** di `harrison2025.pdf`, tre motori di
estrazione Markdown e stabilire, con numeri alla mano, quale usare nel progetto
attuale. In parallelo, verificare se la strategia di estrazione immagini di
`pdfextractor` (Docling `PictureItem`) è migliore di quella attuale (PyMuPDF /
pymupdf4llm) e, in caso, portarla.

I tre motori da testare:

- **A — "docling low-overhead"** (approccio `pdfextractor`): un solo
  `DocumentConverter` riusato, CPU/1-thread/batch-1, `generate_picture_images`,
  de-hyphenation. È la "buona idea" da portare nel progetto attuale.
- **B — "docling attuale"** (approccio `noesis-pdf-reader`): `DocumentConverter()`
  ricreato a ogni pagina, nessuna opzione, per-pagina.
- **C — "granite-docling"**: pipeline VLM `VlmPipeline` di docling 2.x (modello
  `ibm-granite/granite-docling-258M`). Da implementare ex-novo.

## Contesto già acquisito (non rifare l'analisi)

### A/B: differenze già note (vedi anche NEXT_STEPS.md)

| Aspetto | A (pdfextractor) | B (attuale) |
|---|---|---|
| Converter | 1 istanza, riusata | nuova istanza per pagina |
| Granularità | intero documento in 1 passata | 1 pagina per chiamata |
| Accelerator | CPU, `num_threads=1` | default (auto, 4 thread) |
| Batch ocr/layout/table | 1 | 4 (default) |
| Immagini | `generate_picture_images=True`, `images_scale=2.0`, link nel md | assenti |
| De-hyphenation | `clean_text()` | assente |
| Backend PDF | (env var obsoleta) | default |

### C — granite-docling (verificato nell'ambiente)

- docling **2.119.0** espone `docling.pipeline.vlm_pipeline.VlmPipeline`,
  `docling.datamodel.pipeline_options.VlmPipelineOptions` e
  `docling.datamodel.vlm_model_specs` con `GRANITEDOCLING_TRANSFORMERS`.
- Lo spec `GRANITEDOCLING_TRANSFORMERS`: repo `ibm-granite/granite-docling-258M`,
  `inference_framework=transformers`, `max_new_tokens=8192`, `load_in_8bit=True`,
  `llm_int8_threshold=6.0`, `scale=2.0`.
- **Rischio CPU**: `load_in_8bit=True` richiede `bitsandbytes` + CUDA (non
  presenti). Su CPU va forzato `load_in_8bit=False` (override dello spec o
  `VlmPipelineOptions`). Verificare all'avvio con un test su 1 pagina.
- Torch 2.13.0 (CPU), transformers 5.15.0, accelerate 1.14.0 già presenti.
  `vllm` assente (non serve: si usa transformers).
- Il modello (~258M param, fp32 ≈ 1GB su disco) va scaricato la prima volta da
  Hugging Face (~/.cache/huggingface). Serve rete.
- Integrazione minima:
  ```python
  from docling.datamodel.base_models import InputFormat
  from docling.document_converter import DocumentConverter, PdfFormatOption
  from docling.pipeline.vlm_pipeline import VlmPipeline
  converter = DocumentConverter(format_options={
      InputFormat.PDF: PdfFormatOption(pipeline_cls=VlmPipeline)
  })
  md = converter.convert(path, page_range=(p, p)).document.export_to_markdown()
  ```

### Immagini: le due strategie a confronto

- **Attuale (da migliorare)**: `pymupdf` `page.get_images(full=True)` +
  `Document.extract_image(xref)`. Problemi noti: le figure composte vengono
  scomposte nelle XObject componenti (piccole immagini) e vengono estratte anche
  aree di testo/colonne vicino all'immagine.
- **pdfextractor**: Docling `PictureItem` → `element.get_image(document)` che
  **ritaglia la regione della figura dalla pagina renderizzata** (bbox di layout),
  quindi la figura composita resta intera. `generate_picture_images=True`,
  `images_scale=2.0`. Link relativo `images/<pdf>_img_N.png` nel markdown.

## Pagine di test (mappatura)

Pagine richieste (1-based) → indice 0-based pymupdf → `page_range` docling:

| 1-based | 0-based |
|---|---|
| 1007 | 1006 |
| 1009 | 1008 |
| 1013 | 1012 |
| 1015 | 1014 |
| 1018 | 1017 |
| 1019 | 1018 |
| 1022 | 1021 |

## Struttura dei nuovi tool

```
benchmark/
├── __init__.py
├── engines.py           # i 3 motori come funzioni (page→md) + loader lazy
├── metrics.py           # precisione: similarità + check strutturali
├── run_benchmark.py     # CLI: esegue i motori sulle 7 pagine, cronometra,
│                        # salva output + JSON, stampa tabella riepilogo
└── compare_images.py    # confronto estrazione immagini (A vs pymupdf)
```

Output generati in `benchmark/out/<engine>/page_NNNN.md` + `report.json` +
`report.md`. Gli output vanno in `.gitignore` (o in `benchmark/out/`, ignorato).

## Passo 1 — Tool benchmark (`engines.py` + `run_benchmark.py`)

`engines.py` espone una funzione uniforme per motore, così il benchmark è
identico per tutti:

```python
# firma comune: extract(pdf_path, page_1based) -> str (markdown)
extract_docling_low_overhead(pdf_path, page)   # A
extract_docling_current(pdf_path, page)        # B
extract_granite_docling(pdf_path, page)        # C
```

Dettagli per motore:

- **A** — converter lazy creato UNA volta (variabile a livello di modulo):
  ```python
  pipeline_options = PdfPipelineOptions()
  pipeline_options.accelerator_options = AcceleratorOptions(
      num_threads=1, device=AcceleratorDevice.CPU)
  pipeline_options.ocr_batch_size = 1
  pipeline_options.layout_batch_size = 1
  pipeline_options.table_batch_size = 1
  pipeline_options.images_scale = 2.0
  pipeline_options.generate_picture_images = True
  converter = DocumentConverter(format_options={
      InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)})
  ```
  **Niente** `DOCFLOW_PDF_BACKEND` (obsoleto). Se serve forzare pypdfium2:
  `PdfFormatOption(..., pdf_backend=PdfBackend.PYPDFIUM2)`.
  Estrazione con `page_range=(page, page)` su un converter riusato. Applicare
  `clean_text()` (de-hyphenation, portata da `pdfextractor`).
  Nota: per misurare il vantaggio del "documento intero in 1 passata" senza
  convertire le 4273 pagine, il benchmark cronometra anche la conversione delle
  7 pagine in una sola chiamata (`page_range=(1007,1022)` esclusi i buchi non
  presenti nel set) su un campione PDF da 7 pagine (vedi Passo 4).

- **B** — replica fedele di `main._extract_docling`: `DocumentConverter()` NUOVO
  a ogni chiamata, `convert(path, page_range=(page,page))`,
  `export_to_markdown()`, nessuna opzione, nessuna pulizia.

- **C** — `VlmPipeline` con converter riusato; modello scaricato/caricato una
  sola volta. Se `load_in_8bit` fallisce su CPU, override a `False`.
  Per-pagina (il VLM lavora su immagini pagina-per-pagina).

`run_benchmark.py` (CLI, argparse):
- `--pages 1007,1009,1013,1015,1018,1019,1022` (default), `--engines A,B,C`,
  `--repeat N` (default 1; opzionale per media), `--pdf harrison2025.pdf`.
- Per ogni engine: cronometra il **primo** run (include caricamento modelli =
  "cold") e, se N>1, i run successivi ("warm"). Salva md per pagina.
- Stampa tabella: pagina, motore, cold s, warm s, caratteri.
- Scrivi `benchmark/out/report.json`.

## Passo 2 — Metrica di precisione (`metrics.py`)

Automazione + revisione umana. Metriche proposte (default, vedi decisione):

1. **Fedeltà al testo (recall)**: sovrapposizione lessicale con un riferimento
   indipendente. Riferimento = `pymupdf4llm.to_markdown(page)` (veloce, ~0.5s).
   Normalizzare (lowercase, rimuovere formattazione markdown/tabelle/punteggiatura)
   e calcolare F1 su token/4-grammi (chrF) tra output motore e riferimento.
   Misura quanto del testo reale ogni motore cattura (penalizza testo perso o
   allucinato).
2. **Check strutturali** per pagina: presenza titoli (`#`), tabelle (`| --- |`),
   immagini (`![`, solo A/C se abilitate), e stringhe-chiave note della pagina
   (es. pagina 1007: "corresponding human", "cellular responses",
   "FURTHER READING", "TABLE 124-5").
3. **Ordine di lettura**: per le pagine a due colonne, verificare che il blocco
   introduttivo preceda il corpo (asserzioni mirate, come già in
   `tests/test_fixes.py`).
4. **Revisione umana**: gli output md restano in `benchmark/out/` per il
   confronto visivo del committente.

Ogni metrica produce un punteggio per pagina → media per motore nel report.

## Passo 3 — Confronto immagini (`compare_images.py`)

Su ciascuna delle 7 pagine:

1. **Strategia attuale (pymupdf)**: `page.get_images(full=True)` +
   `doc.extract_image(xref)` → salva PNG, conta e segna le dimensioni/bbox.
2. **Strategia pdfextractor (docling)**: gira il motore A con
   `generate_picture_images=True`, itera `PictureItem` e salva
   `element.get_image(document)` → PNG, conta e segna i bbox.

Confronto per pagina: numero immagini, area coperta, se le figure composte
restano intere (docling) vs scomposte (pymupdf), e se vengono estratte solo
figure vere (niente ritagli di testo). Output: `benchmark/out/images/…` +
riepilogo. Verdetto numerico + campioni visivi per il committente.

## Passo 4 — Campione PDF piccolo (per la modalità "intero documento" di A)

Per misurare il vero vantaggio di A (intero documento in 1 passata) senza
convertire 4273 pagine: creare un campione con le sole 7 pagine via pymupdf
`insert_pdf`, salvarlo in `/tmp` (o `benchmark/out/sample_7.pdf`, ignorato), e
far girare A su di esso in una passata. Tenere traccia della mappatura
pagina-sorgente → pagina-campione.

## Passo 5 — Esecuzione e report

Ordine consigliato (dal più economico al più costoso):
1. B (attuale) sulle 7 pagine — baseline ~25-30s/pag.
2. A (low-overhead) sulle 7 pagine — atteso più veloce (converter riusato).
3. A sull'intero campione da 7 pagine (1 passata).
4. C (granite-docling): scaricare il modello, test su 1 pagina, poi sulle 7.
5. Confronto immagini.

Scrivere `benchmark/out/report.md` con: tabella tempi (cold/warm, per pagina e
totali), tabella precisione per motore, esiti strutturali, verdetto immagini,
conclusioni e raccomandazione.

## Passo 6 — Portare le cose buone in `main.py` (dopo il benchmark)

Sulla base dei risultati, in ordine di rapporto costo/beneficio:
1. **Riutilizzare `DocumentConverter`** (istanza lazy `self._docling_converter`).
2. **Opzioni CPU/low-RAM** dal motore A (se confermate vantaggiose).
3. **De-hyphenation** `clean_text()` come voce del dropdown "Fix:" + test.
4. **Immagini** (se il confronto lo giustifica): `generate_picture_images` +
   `PictureItem` + placeholder nel viewer.
5. **Backend granite-docling** come nuova voce backend (se qualità/velocità lo
   giustificano su CPU).

## Vincoli e rischi

- NON convertire `harrison2025.pdf` per intero.
- NON modificare file fuori da `noesis-pdf-reader/` (`pdfextractor` è solo da
  leggere/importare).
- Rispettare convenzioni: helper `_`-prefissi, dropdown con costanti di classe
  (`BACKENDS`, `FIXES`), test in `tests/`.
- Il download del modello granite (~1GB) e l'inferenza CPU richiedono rete,
  disco e tempo: confermare prima con il committente.
- `load_in_8bit` su CPU: possibile fallback `False` richiesto.
- Tempi attesi lordi: B ≈ 7×25-30s ≈ 3-4 min; A ≈ 7×~20s (stima) ≈ 2-3 min;
  C ≈ 7×? (VLM CPU, primi run lenti). Totale gestibile ma da eseguire in
  background se necessario.

## Criteri di accettazione

- `tests/` ancora verde (`.venv/bin/python -m unittest discover -s tests -v`).
- I 3 motori girano sulle stesse 7 pagine e producono output salvati + report.
- Il report contiene tempi e metriche di precisione per motore, e un verdetto
  sulla strategia immagini.
- Le eventuali modifiche a `main.py` sono coperte da test e non rompono i
  backend esistenti.
