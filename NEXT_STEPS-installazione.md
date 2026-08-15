# Istruzioni per l'agente — decisione sull'installazione / distribuzione

Redatto il 2026-08-15. Leggere tutto prima di iniziare.

## Obiettivo

Decidere e applicare **come si installa/distribuisce** noesis-pdf-reader.
La decisione è stata rimandata ("lasciamo queste decisioni a domani"): questo
file contiene tutto il contesto già acquisito e i passi per chiudere senza
rifare analisi.

## Stato attuale (già fatto e verificato — NON rifare)

Il lavoro di packaging PyInstaller è **già implementato e validato su Linux**,
ma **non committato** (tutto in working tree):

- File creati:
  - `packaging/requirements-lite.txt` (backend veloci, no docling/torch)
  - `packaging/requirements-full.txt` (lite + `docling>=2.0`)
  - `packaging/noesis.spec` (PyInstaller onedir, parametrizzato da `NOESIS_FULL`)
  - `packaging/build.sh` e `packaging/build.ps1` (build locale lite/full)
  - `.github/workflows/release.yml` (matrice windows-latest / ubuntu-latest /
    macos-14 [Apple Silicon arm64] × lite/full; su tag `v*` crea la Release)
- File modificati:
  - `main.py`: cartella immagini via `QStandardPaths.AppDataLocation`
    (prima era `~/.local/share/...`, sbagliato su Windows/macOS);
    `applicationName` = `noesis-pdf-reader`.
  - `.gitignore`: + `.venv-build/`, `*.zip`, `*.tar.gz`, `*.dmg`.
  - `README.md`: sezione "Generare una release".
- Validazione eseguita su Linux (headless):
  - **lite** = 359 MB → build OK, app parte senza errori. Risolto un bug reale:
    mancavano i modelli ONNX di pymupdf4llm (49 MB) → aggiunto
    `collect_data_files("pymupdf")` nello spec.
  - **full** = 1.6 GB → build OK, app parte senza errori. Nel bundle presenti
    torch CPU, transformers, docling (+ `docling_parse` `.so`), tokenizers,
    safetensors, onnxruntime. **Nessuna libreria CUDA pesante** (niente
    libcublas/cudnn/nccl; solo uno stub `libcudart` innocuo di torchvision).
- Da NON committare: `page_1007_pymupdf4llm.md` e `page_1007_render.png`
  (artefatti di debug, ancora untracked).

## Fatti chiave (già misurati — NON rifare)

1. **Il peso non dipende dal packaging, ma da torch/docling**:
   - lite (backend veloci) ≈ 350–400 MB
   - full (docling + torch CPU) ≈ 1.5 GB
   - full (docling + torch CUDA) ≈ 6 GB
2. **Il `requirements.txt` attuale è quello PESANTE**: `docling>=2.0` installa
   torch CUDA di default (~6 GB di venv). Quindi "requirements.txt e basta"
   NON è automaticamente leggero.
3. torch **CPU-only** si installa SOLO con
   `pip install torch --index-url https://download.pytorch.org/whl/cpu`
   (mai dai default di PyPI per le release).
4. PyInstaller: usare Python **3.12** (già nel workflow), build **onedir**
   (niente onefile: troppo lento/pesante con torch), `macos-14` = Apple
   Silicon arm64.
5. Il binario macOS è firmato solo ad-hoc; per distribuzione serve firma +
   notarizzazione Apple (fuori scope).

## La decisione da prendere (3 opzioni)

Chiedere all'utente, proponendo la raccomandazione:

- **A — Solo source + requirements.txt**: rendere `requirements.txt` leggero
  (backend veloci), Docling come extra opzionale, e **rimuovere** `packaging/`
  e `.github/workflows/release.yml`.
- **B — Source primario + CI opzionale** (RACCOMANDATA): come A, ma si tiene il
  setup PyInstaller/CI già pronto (costo zero) per un eventuale binario futuro.
- **C — Lascia tutto com'è**: requirements.txt pesante (docling+CUDA) +
  PyInstaller, senza modifiche.

Motivo della raccomandazione B: finora il progetto è sempre stato eseguito da
sorgente con venv; l'utente è tecnico. Il percorso source+requirements è più
semplice, leggero da distribuire e sempre aggiornato, ma tenere il CI pronto
non costa nulla.

## Passi da eseguire (a seconda della scelta)

### Se A o B — riorganizzare i requirements

1. Riscrivere `requirements.txt` **leggero** copiando il contenuto di
   `packaging/requirements-lite.txt` (rimuovere `docling>=2.0`).
2. Creare `requirements-docling.txt` con:
   ```
   -r requirements.txt
   docling>=2.0
   ```
   e aggiungere nel README l'istruzione torch CPU:
   `pip install torch --index-url https://download.pytorch.org/whl/cpu`
   prima di `pip install -r requirements-docling.txt`.
3. Aggiornare il README: sezione "Installazione" con due percorsi (lite e
   full) + esecuzione `python main.py`.

### Se A — rimuovere il packaging

1. `git rm -r packaging/ .github/workflows/release.yml`
2. Ripulire `.gitignore` dalle voci aggiunte per il packaging (`.venv-build/`,
   `*.zip`, `*.tar.gz`, `*.dmg`) **solo se** non servono più.
3. Rimuovere dal README la sezione "Generare una release".

### Se B — tenere il packaging

1. Verificare che `packaging/requirements-lite.txt` sia coerente con il nuovo
   `requirements.txt` (stesso contenuto).
2. Aggiornare il README: l'installazione primaria è source+requirements; la
   sezione "Generare una release" resta come opzionale.

### In tutti i casi

1. `.venv/bin/python -m unittest discover -s tests -v` → verde.
2. Commit e push (chiedere conferma all'utente, come da convenzione).
3. NON committare `page_1007_pymupdf4llm.md` e `page_1007_render.png`.

## Criteri di accettazione

- `requirements.txt` è leggero (niente docling/torch) se scelta A/B.
- `requirements-docling.txt` esiste e documenta torch CPU.
- README spiega entrambi i percorsi di installazione.
- Test verdi.
- Se A: `packaging/` e il workflow non esistono più. Se B: restano e il
  workflow non è rotto (YAML valido).
