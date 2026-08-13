# Istruzioni per l'agente — prossimo passo (Docling: confronto + porting)

Redatto il 2026-08-13. Leggere tutto prima di iniziare.

## Obiettivo

Confrontare, **sulla stessa pagina PDF**, l'estrazione Docling dei due
progetti, stabilire quale è più corretta e più performante, e portare nel
progetto attuale le cose buone di `pdfextractor`.

- Progetto attuale: `noesis-pdf-reader/` (viewer PyQt6, estrazione **a pagina singola**).
- Progetto precedente: `../pdfextractor/` (CLI, estrazione **dell'intero documento**).

## Contesto già acquisito (non rifare questa analisi)

### Come usa Docling ciascun progetto

**Vecchio — `../pdfextractor/pdf_to_md/converter.py`**
```python
os.environ["DOCFLOW_PDF_BACKEND"] = "pypdfium2"      # ← OBSOLETO in docling 2.x
AcceleratorOptions(num_threads=1, device=CPU)          # CPU + 1 thread
PdfPipelineOptions(ocr/layout/table_batch_size=1)      # low-RAM
images_scale=2.0, generate_picture_images=True
converter = DocumentConverter(format_options={...})    # 1 sola istanza
result = converter.convert(pdf_path)                   # TUTTO il doc in una passata
# + estrazione immagini (PictureItem) + placeholder
# + clean_text(): de-hyphenation  re.sub(r'(\w)-\s*\n\s*(\w)', r'\1\2', text)
```

**Nuovo — `main.py` → `_extract_docling` (riga ~1235)**
```python
converter = DocumentConverter()                        # nessuna opzione, NUOVA istanza ogni pagina
result = converter.convert(path, page_range=(page+1, page+1))
md = result.document.export_to_markdown()              # niente immagini, niente de-hyphenation
```

### Differenze da attenzionare (già individuate)

1. **Granularità**: vecchio = documento intero in 1 passata; nuovo = 1 pagina
   per chiamata. Il modello layout di Docling, su tutto il documento, vede
   header/footer, tabelle che continuano tra pagine, ordine di lettura a
   serpente. Il per-pagina perde quel contesto.
2. **Caching converter**: il nuovo ricrea `DocumentConverter()` a ogni pagina
   → ricarica layout/table model a ogni navigazione (causa dei ~25s/pag).
3. **Ottimizzazioni CPU/low-RAM perse**: il nuovo usa i default di docling
   (device `auto`, `num_threads=4`, batch=4). Il vecchio forzava CPU/1/batch1
   (scelta deliberata per <4GB, vedi README di pdfextractor).
4. **De-hyphenation assente** nel nuovo (il vecchio ha `clean_text()`).
5. **Immagini/figures**: il vecchio le estrae e linka; il nuovo le perde.
6. **`DOCFLOW_PDF_BACKEND` è morto** in docling 2.119 (grep fatto: nessun uso
   nel pacchetto). Se si porta il vecchio codice, il backend va scelto con
   `PdfFormatOption(..., pdf_backend=PdfBackend.PYPDFIUM2)`, NON con l'env var.

### Ambiente verificato

- docling 2.119.0, docling-core 2.91.0, docling-ibm-models 3.14.0, pypdfium2 5.12.1, pymupdf 1.28.2 (nel `.venv` del progetto attuale).
- Default `PdfPipelineOptions` in 2.119: `images_scale=1.0`, `generate_picture_images=False`, `do_ocr=True`, `ocr/layout/table_batch_size=4`, `num_threads=4`, `device=auto`.
- Import verificati OK in 2.119: `AcceleratorOptions`, `AcceleratorDevice`, `PdfPipelineOptions`, `PictureItem` (dal vecchio progetto).
- `pdfextractor` NON ha un suo venv: riusare il `.venv` del progetto attuale per eseguire entrambe le varianti.

## Passo 1 — Preparare un campione PDF piccolo

NON convertire `harrison2025.pdf` per intero (290MB, 1000+ pagine). Estrarre
con pymupdf un campione di poche pagine, ad es. pagine 1005–1008 (contiene la
pagina 1007 del caso studio a due colonne + tabella 124-5). Esempio:

```python
import pymupdf
src = pymupdf.open("harrison2025.pdf")
dst = pymupdf.open()
dst.insert_pdf(src, from_page=1004, to_page=1007)   # 0-based → pagine 1005..1008
dst.save("/tmp/campione_1005_1008.pdf")
```

Nota: la pagina 1007 nel PDF = indice 0-based 1006 → nel campione diventa la
pagina 3 (indice 2). Tenere traccia della mappatura.

## Passo 2 — Eseguire le due estrazioni e misurarle

1. **Vecchio approccio** (importando dal progetto precedente, nel `.venv` attuale):
   ```python
   import sys; sys.path.insert(0, "../pdfextractor")
   from pdf_to_md.converter import convert_pdf
   md_old = convert_pdf("/tmp/campione_1005_1008.pdf", "/tmp/out_old")
   ```
   Misurare tempo e (se possibile) picco di memoria.

2. **Nuovo approccio** (per la stessa pagina):
   ```python
   import main
   # main.MainWindow richiede QApplication: meglio replicare _extract_docling
   # a mano, oppure istanziare la finestra in headless (QT_QPA_PLATFORM=offscreen).
   ```
   Misurare tempo per UNA pagina e per le 4 pagine del campione (per mostrare
   il costo del converter ricreato a ogni pagina).

Salvare gli output in `/tmp/` e un riepilogo in `docs/docling-comparison.md`
(da creare) con: tempo, dimensioni, e confronto testuale della pagina 1007
(ordine paragrafo introduttivo, riferimenti "FURTHER READING", titolo capitolo,
tabella 124-5).

## Passo 3 — Verdetto: correttezza e performance

Rispondere a due domande, con evidenza:

- **Correttezza**: quale output rispetta l'ordine di lettura reale (a serpente,
  due colonne) e rende tabelle/titoli nel posto giusto?
- **Performance**: quanto incide il per-pagina vs intero-documento e il
  converter non riusato? (Atteso: il vecchio è più veloce per documento intero
  perché carica i modelli una volta sola.)

## Passo 4 — Portare le cose buone nel progetto attuale

In ordine di rapporto costo/beneficio (fare almeno i primi 3):

1. **Riutilizzare `DocumentConverter`** (istanza lazy una sola volta, tipo
   `self._docling_converter`, non ricrearlo in `_extract_docling`).
2. **Opzioni CPU/low-RAM**: `AcceleratorOptions(num_threads=1, device=CPU)` +
   `ocr/layout/table_batch_size=1` (portarle dal vecchio, ma via
   `PdfFormatOption`, senza `DOCFLOW_PDF_BACKEND`).
3. **De-hyphenation**: portare `clean_text()` come nuova voce del dropdown
   "Fix:" (o applicarla di default al backend Docling). Aggiungere test.
4. **Immagini** (opzionale, decidere col committente): `generate_picture_images`
   + estrazione `PictureItem` + placeholder. Verificare se serve nel viewer.
5. **Backend PDF**: se si vuole forzare pypdfium2, usare `PdfBackend.PYPDFIUM2`
   in `PdfFormatOption`.

## Vincoli e rischi

- NON eseguire conversioni full-document su `harrison2025.pdf`.
- NON modificare file fuori da `noesis-pdf-reader/` (il progetto `pdfextractor`
  è di riferimento, va solo letto/importato).
- Il backend Docling resta lento su CPU (~25s/pag): l'obiettivo è ridurre il
  costo di ricaricare i modelli a ogni pagina, non renderlo "istantaneo".
- Rispettare le convenzioni esistenti: single-file `main.py`, helper `_`-prefissi,
  dropdown con costanti di classe (`FIXES`, `BACKENDS`).

## Criteri di accettazione

- `tests/` ancora verde (`.venv/bin/python -m unittest discover -s tests -v`).
- Docling riusa il converter (navigare 2 pagine diverse NON ricarica i modelli
  due volte — verificabile con log/timing).
- De-hyphenation attiva e coperta da un test.
- `docs/docling-comparison.md` compilato con numeri e conclusioni.

## Nota (già risolto oggi)

Il bug "con Riordino colonne le tabelle Markdown non vengono renderizzate"
era causato dalla caption incollata alla riga di intestazione (nessuna riga
vuota → l'estensione `tables` di python-markdown saltava l'intera tabella).
Fixato in `_table_to_md` (riga vuota dopo la caption) + test di regressione
`test_caption_keeps_blank_line_so_table_renders`. Verificato: pagine 1007 e 156
ora producono `<table>`.
