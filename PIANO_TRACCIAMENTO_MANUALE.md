# Piano — Definitore di layout manuale (implementazione)

Redatto il 2026-08-16. **Da implementare in una sessione successiva.** Completa lo
[studio](STUDIO_TRACCIAMENTO_MANUALE.md); qui ci sono le specifiche concrete del
prototipo, i test e l'integrazione nel programma già funzionante.

**Dipendenza**: va fatto *dopo* il prototipo dell'engine adattativo
([PIANO_ENGINE_ADATTATIVO.md](PIANO_ENGINE_ADATTATIVO.md)), così il tracciato ha già
un posto dove agganciarsi (il fix `manual_regions` nel registro).

## Obiettivo

Permettere all'utente di tracciare col mouse, sulla pagina renderizzata, **regioni
tipizzate** (testo/tabella/immagine/ignora) e definirne la **sequenza di lettura**,
salvare il tracciato per pagina, e far sì che l'estrazione della pagina usi il
tracciato come override dell'auto.

## Struttura dei file

```
main.py            # esistente: PdfPageView (overlay/strumenti) + MainWindow (toolbar,
                   #           persistenza, estrazione per regione, aggancio _apply_fix)
tests/test_manual_layout.py   # NUOVO: unitari + integrazione
```

Tutto resta in `main.py` (coerente col progetto single-file), tranne il modulo
`layout_engine.py` del task 2 che il fix `manual_regions` andrà a usare.

## Modello dati (in `main.py`, livello modulo, puro)

```python
from dataclasses import dataclass, field

REGION_KINDS = ("text", "table", "image", "ignore")

@dataclass
class Region:
    kind: str                                  # "text"|"table"|"image"|"ignore"
    bbox: tuple[float, float, float, float]    # punti PDF (x0,y0,x1,y1)
    order: int = 0                             # sequenza di lettura

@dataclass
class ManualLayout:
    pdf: str                                   # nome file (es. "cecil2024.pdf")
    page: int                                  # 1-based
    regions: list[Region] = field(default_factory=list)
```

Helper puri (testabili senza GUI):

```python
def layout_path_for(pdf_stem: str, page: int) -> Path:
    """Cartella dati app (QStandardPaths.AppDataLocation) / layouts / <pdf> / page_NNNN.json."""

def save_layout(layout: ManualLayout) -> None: ...
def load_layout(pdf_stem: str, page: int) -> ManualLayout | None: ...
def delete_layout(pdf_stem: str, page: int) -> None: ...
```

La cartella base è la stessa già usata per le immagini docling (`_get_images_dir`),
quindi si riusa la logica `QStandardPaths`.

## Estrazione per regione (funzione pura, testabile)

```python
def apply_manual_layout(page, layout: ManualLayout) -> str:
    """Ricostruisce il markdown della pagina a partire dalle regioni, in ordine.

    - text   → page.get_text(clip=bbox, sort=True) + de-sillabazione
    - table  → tabella find_tables() la cui bbox interseca la regione, via _table_to_md
               (fallback: get_textbox(clip))
    - image  → _extract_image_region già esistente → link markdown ![](file://…)
    - ignore → saltata
    Le regioni vengono concatenate in ordine di `order` (a parità, ordine di disegno).
    """
```

Nota: per una regione `text` molto larga che contiene a sua volta più colonne, nel
prototipo si estrae in ordine naturale (`get_text(sort=True)`); la granularità fine
spetta all'utente (disegna due regioni separate). Coerente con lo studio.

## PdfPageView: strumenti e overlay

Oggi `PdfPageView` ha già rubber-band + `region_selected`. Si generalizza:

```python
class PdfPageView(QGraphicsView):
    # segnali (aggiunta)
    region_added = pyqtSignal(str, float, float, float, float)  # kind, x0,y0,x1,y1
    region_clicked = pyqtSignal(int)                            # indice regione (sequenza)

    def set_tool(self, tool: str | None):
        """None=normale; "select"=zona immagine (esistente); "text"|"table"|
        "image"|"ignore"=disegna regione; "sequence"=clicca per riordinare."""

    def show_regions(self, regions: list[Region]): ...   # ridisegna overlay
    def clear_regions(self): ...
```

Dettagli:

- **Overlay**: ogni regione = `QGraphicsRectItem` (penna colore per tipo:
  text=verde, table=arancio, image=viola, ignore=rosso) + `QGraphicsTextItem` col
  numero d'ordine. Item tenuti in `self._region_items`.
- **Disegno**: `set_tool("text"|"table"|"image"|"ignore")` riusa il flusso
  `mousePress/Move/Release` esistente; al release emette `region_added` invece di
  `region_selected`.
- **Conversione coordinate**: come oggi in `_on_region_selected`, dividere per
  `self._render_scale` per ottenere punti PDF; il `region_added` emette già in punti
  PDF (o lo converte il chiamante — scegliere e tenere un solo punto di conversione).
- **Sequenza**: in `set_tool("sequence")` il click su un `QGraphicsRectItem`
  (hit-test sugli item della scena) emette `region_clicked(indice)`; il chiamante
  riassegna `order` in ordine di click.
- **Sicurezza**: tutti i gestori mouse restano dentro try/except (PyQt6: un'eccezione
  in un handler virtuale abortisce il processo — lezione già appresa).

## Toolbar (sopra il visualizzatore PDF)

Estendere `_build_page_toolbar` con un gruppo di strumenti **mutuamente esclusivi**
(`QActionGroup`/`QButtonGroup`), oltre al pulsante esistente "🖱️ Seleziona zona":

| Bottone | tool | effetto |
|---|---|---|
| 🖱️ Seleziona zona (esistente) | `select` | estrazione immagine al volo (invariato) |
| 📄 Testo | `text` | disegna regione testo |
| 📊 Tabella | `table` | disegna regione tabella |
| 🖼️ Immagine | `image` | disegna regione immagine |
| 🚫 Ignora | `ignore` | disegna regione da ignorare |
| 🔢 Sequenza | `sequence` | clicca regioni per riordinarle |
| ▶ Applica manuale | — | ri-estrae la pagina col tracciato |
| 💾 Salva | — | persiste il tracciato (auto-salva anche al cambio pagina) |
| 🧹 Pulisci | — | elimina il tracciato della pagina |

Stato in `MainWindow`: `self._manual_layout: ManualLayout | None` per la pagina
corrente, caricato/salvato su `_set_page`.

## Integrazione con l'engine adattativo

Nel registro `FIX_REGISTRY` (task 2) aggiungere **un fix in testa**, priorità 0:

```python
Fix("manual_regions", "…", 0,
    when=lambda p, b: manual_override_exists(p.pdf, p.page),
    apply=apply_manual_layout)
```

`manual_override_exists` = esiste il file `layouts/<pdf>/page_NNNN.json`.

In `_apply_fix` (MainWindow), PRIMA di ogni altro ramo:

```python
override = load_layout(self._pdf_path.stem, page_num + 1)
if override is not None and fix == "Auto":        # o sempre, vedi nota
    return apply_manual_layout(doc[page_num], override) or text
```

**Decisione da confermare in implementazione**: il manuale vale **solo in `Auto`**
(scelta conservativa) oppure **sempre** (override assoluto). Consiglio: *sempre*,
perché è esattamente ciò che l'utente vuole quando traccia a mano; ma partiamo da
`solo Auto` per non cambiare il comportamento dei fix manuali esistenti, e lo
rendiamo un'opzione se serve.

## Persistenza e ciclo di vita

- Caricamento del tracciato della pagina in `_set_page` (dopo `_display_page`).
- Salvataggio su "💾 Salva" e automaticamente quando si cambia pagina / si chiude.
- "🧹 Pulisci" cancella il file e torna all'estrazione auto.
- Il cambio backend/fix **non** tocca il tracciato; il tracciato vince finché esiste
  (secondo la decisione sopra).

## Retrocompatibilità

- "🖱️ Seleziona zona" resta identico (stesso flusso `region_selected` → immagine).
- Nessuna modifica al comportamento di pagine **senza** tracciato.
- I fix manuali esistenti (`Riordino colonne`, ecc.) restano invariati.

## Test (`tests/test_manual_layout.py`)

Unitari (pagine sintetiche pymupdf, come `test_fixes.py`):

1. `apply_manual_layout`: 2 regioni testo + 1 ignore → output contiene i due testi
   nell'ordine giusto e **non** quello ignorato.
2. `apply_manual_layout`: regione tabella → `| --- |` presente (usa `_add_table`).
3. `apply_manual_layout`: regione immagine → link `![` presente.
4. `save_layout`/`load_layout` round-trip su dir temporanea (`tmp_path`).
5. `delete_layout` rimuove il file.
6. Conversione scena→punti PDF: con `_render_scale=3`, una regione scena
   (0,0,300,300) → bbox PDF (0,0,100,100).

Integrazione (MainWindow, headless `QT_QPA_PLATFORM=offscreen`):

7. Pagina con tracciato salvato → `_apply_fix(..., "Auto")` usa il manuale (verificato
   con un testo "ignora" assente e ordine corretto).
8. Pagina senza tracciato → `_apply_fix` si comporta come oggi (nessun override).

Regressione: i 43 test esistenti restano verdi; il flusso "Seleziona zona" resta
verde (test già presenti in `test_images.py`).

## Criteri di accettazione

- `tests/` verde: 43 esistenti + i nuovi di `test_manual_layout.py`.
- L'utente può: disegnare 4 tipi di regione, riordinarle, applicarle, salvarle,
  cancellarle; la pagina si estrae secondo l'ordine dato.
- "Seleziona zona" invariato; pagine senza tracciato invariate.
- Il tracciato persiste tra le sessioni e tra le pagine.

## Fuori scope (fase 2)

- "💾 Salva **regola**" (generalizzare il tracciato in `fix_rules.json`) e il wizard:
  arrivano dopo, quando engine adattativo + tracciato manuale sono entrambi pronti.
- Snapping delle regioni ai blocchi auto-rilevati.
- Regioni poligonali/non rettangolari.

## Rischi

1. **Hit-test della sequenza**: cliccare esattamente un rettangolo è scomodo; usare
   tolleranza (rileva l'item più vicino entro ~8px) e un rettangolo più spesso in
   modalità "sequence".
2. **Coordinate**: tenere un solo punto di conversione scena↔PDF per evitare
   disallineamenti tra disegno ed estrazione.
3. **Sovrapposizioni/annidamenti**: vietarle nel prototipo (se due regioni si
   intersecano > 20% area, rifiutare l'ultima con messaggio).
4. **Persistenza sporca**: "Pulisci" e un elenco dei tracciati esistenti devono
   essere raggiungibili (evitare pagine "strane" senza capire perché).
