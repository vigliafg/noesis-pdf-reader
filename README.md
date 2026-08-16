# noesis-pdf-reader

La versione di Noesis che estrae e traduce testi da pagine PDF.

App desktop **PyQt6** con vista affiancata: pagina renderizzata (sinistra) +
testo estratto in markdown (destra), con più motori di estrazione
selezionabili a runtime, traduzione automatica e gallery delle figure.

## Tre tier di installazione

| Tier | Contenuto | File |
|---|---|---|
| **light** | backend veloci (PyMuPDF4LLM ⚡ + fix layout + estrazione immagini) | `requirements.txt` |
| **medium** | light + Docling 🧠 su torch **CPU-only** (niente CUDA) | `requirements-docling.txt` |
| **full** | medium + torch **CUDA** (GPU) | `requirements-cuda.txt` |

### Light (default)

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
.venv/bin/python main.py                      # Windows: .venv\Scripts\python
```

### Medium (Docling su CPU)

```bash
# torch CPU-only va installato PRIMA, da un index dedicato (mai da PyPI,
# che di default scarica la build CUDA ~6 GB):
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements-docling.txt
.venv/bin/python main.py
```

### Full (Docling su GPU)

```bash
# Installa torch con CUDA (adatta l'index alla versione CUDA del driver):
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu124
.venv/bin/pip install -r requirements-cuda.txt
.venv/bin/python main.py
```

## Backend di estrazione

| Backend | Velocità | Qualità | Note |
|---|---|---|---|
| PyMuPDF4LLM ⚡ | ~0.5 s/pag | buona | default, anteprima istantanea |
| Docling 🧠 | ~2–4 s/pag (CPU) | ottima | figure intere via `PictureItem`, de-hyphenation; richiede torch |
| Smart (tabelle) | ~1 s/pag | buona | pdfplumber + tabelle |
| pdf_oxide 🦀 / pdfminer.six / pypdfium2 / pdfplumber | — | base | backend alternativi |

Il fix layout **"Auto"** (default) riordina le colonne solo quando la pagina
lo richiede (euristica geometrica) e non tocca l'output di Docling.

## Estrazione immagini

- **Automatica**: con i backend non-Docling, le figure embedded della pagina
  vengono estratte via PyMuPDF e mostrate nella tab **🖼️ Immagini**.
- **Manuale a zone**: il pulsante **🖱️ Seleziona zona** attiva una rubber-band
  sul pannello sinistro; l'immagine sotto la zona selezionata viene estratta
  (raster originale se copre tutta la selezione, altrimenti render della zona,
  utile per figure composite/vettoriali) e aggiunta alla gallery, con
  **💾 Salva** e **📋 Copia**.

## Generare una release (PyInstaller)

Le release producono **due binari** per piattaforma:

- **`light`** — backend veloci, senza Docling/torch (binario ~150–250 MB).
- **`medium`** — include Docling (torch **CPU-only**, niente CUDA, binario
  ~1.5–2 GB). I modelli Docling vengono scaricati al primo utilizzo.

Il tier **`full`** (CUDA) è **solo da sorgente**: non viene impacchettato,
perché le librerie CUDA (~2.7 GB) non entrano nel binario. Si installa con
`requirements-cuda.txt` (vedi sopra).

### Automatico (GitHub Actions)

Push di un tag `v*` (es. `v1.2.0`) → il workflow
[`.github/workflows/release.yml`](.github/workflows/release.yml) builda
nativamente su `windows-latest`, `ubuntu-latest` e `macos-14` (Apple Silicon)
le varianti `light` e `medium` e allega gli archivi alla GitHub Release.
Disponibile anche con trigger manuale (tab *Actions → Run workflow*).

### Manuale

```bash
# Linux / macOS
packaging/build.sh light      # oppure: packaging/build.sh medium

# Windows (PowerShell)
.\packaging\build.ps1 -Variant light
.\packaging\build.ps1 -Variant medium
```

Output in `dist/NoesisPDFReader` (`.app` su macOS). Gli script creano un venv
isolato in `.venv-build/` e, per la variante `medium`, installano torch
CPU-only da `https://download.pytorch.org/whl/cpu`.

> **Nota macOS**: il `.app` è firmato solo ad-hoc; per distribuirlo via
> download serve firma + notarizzazione Apple (account developer). Al primo
> avvio locale puoi aggirare Gatekeeper con `xattr -cr NoesisPDFReader.app`.

## Test

```bash
.venv/bin/python -m unittest discover -s tests -v
```
