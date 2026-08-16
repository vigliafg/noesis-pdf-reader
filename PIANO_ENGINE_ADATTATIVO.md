# Piano — Prototipo engine adattativo dei fix (implementazione)

Redatto il 2026-08-16. **Da implementare in una sessione successiva.** Completa lo
[studio](STUDIO_ENGINE_ADATTATIVO.md); qui ci sono solo le specifiche concrete del
prototipo, i test e l'integrazione finale nel programma già funzionante.

## Obiettivo

Introdurre il motore "Profilo → Piano → Pipeline" descritto nello studio, senza
cambiare il comportamento visibile degli attuali fix del combo `FIXES`, e con
`Auto` che diventa davvero adattativo per ogni backend (oggi salta docling).

## Struttura dei file

```
main.py               # esiste: _apply_fix viene riscritto per chiamare l'engine
layout_engine.py      # NUOVO: profilo + registro fix + scheduler (il "database")
fix_rules.json        # NUOVO (opzionale): override utente delle regole (se presente)
tests/test_layout_engine.py   # NUOVO: test unitari + integrazione
```

`layout_engine.py` NON importa PyQt: è puro (pymupdf + tipi), quindi testabile senza
GUI. `main.py` importa da lì.

## `layout_engine.py` — firme

### Profilo (puro, deterministico)

```python
from dataclasses import dataclass, field
from typing import Callable, Sequence

@dataclass(frozen=True)
class LayoutProfile:
    columns: int                      # 1..N (0 = pagina vuota/non testuale)
    splits: tuple[float, ...]         # N-1 confini di colonna
    columns_overlap: bool             # le colonne si affiancano verticalmente
    has_tables: bool                  # find_tables ha trovato tabelle dati
    full_width_tables: int            # tabelle a tutta larghezza
    has_small_text: bool              # blocchi body < 7.5pt (referenze/indici)
    has_references: bool              # entry numerate "1." "2." in ≥2 colonne, font piccolo
    has_index: bool                   # ≥3 colonne + righe corte + suffissi numerici
    body_blocks: int                  # blocchi di testo fuori dalle tabelle

def profile_page(page) -> LayoutProfile:
    """Ispeziona la pagina UNA volta con pymupdf e restituisce il profilo."""
```

I campi riusano i segnali già presenti: `_collect_blocks`, `_detect_column_splits`,
`find_tables`, larghezze/dimensioni font. `has_references` e `has_index` sono nuove
euristiche leggere (vedi "euristiche nuove" sotto).

### Registro fix (il "database")

```python
@dataclass(frozen=True)
class Fix:
    id: str
    description: str
    order: int                        # priorità nel piano
    when: Callable[[LayoutProfile, str], bool]   # (profile, backend)
    apply: Callable[..., str]         # (md, page, profile) -> md

FIX_REGISTRY: Sequence[Fix] = (...)   # i fix built-in, come dati
```

### Scheduler + applicazione

```python
def plan_fixes(profile: LayoutProfile, backend: str, mode: str = "auto") -> list[Fix]:
    """Ordina i fix del registro i cui `when` sono veri (con override utente)."""

def apply_plan(md: str, page, profile: LayoutProfile, plan: Sequence[Fix]) -> str:
    """Applica in ordine: md = fix.apply(md, page, profile)."""
```

`mode` accetta `"auto"` oppure l'`id` di un singolo fix (per il combo manuale).

## I fix built-in (mappa 1:1 sulle funzioni esistenti)

| id | description | quando (auto) | applica |
|---|---|---|---|
| `dehyphenate` | de-sillabazione/pulizia | `backend == docling` | `clean_text` |
| `split_glued` | ri-spezza incollati a cavallo colonne | `backend == docling and columns >= 2` | `_split_cross_column_paragraphs` |
| `reorder_columns` | riordino N colonne + tabelle | `backend != docling and columns >= 2 and columns_overlap` | `_column_aware_markdown(..., move_title=False)` |
| `reorder_columns_title` | come sopra + titolo in testa | (solo manuale) | `_column_aware_markdown(..., move_title=True)` |
| `spacing` | fix cosmetici spaziatura | sempre (priorità più bassa) | `_spacing_fixes` |

I primi tre sono **già implementati e testati**; il prototipo li *incapsula* soltanto,
non li riscrive.

## Tabella regole default (dentro `FIX_REGISTRY`)

È la traduzione dichiarativa del vecchio `Auto`, più il miglioramento docling:

```python
FIX_REGISTRY = [
    Fix("dehyphenate",          "…", 10, lambda p, b: b == "Docling 🧠", clean_text),
    Fix("split_glued",          "…", 20, lambda p, b: b == "Docling 🧠" and p.columns >= 2,
        _split_cross_column_paragraphs),
    Fix("reorder_columns",      "…", 30, lambda p, b: b != "Docling 🧠" and p.columns >= 2 and p.columns_overlap,
        lambda md, page, p: _column_aware_markdown(page) or md),
    Fix("spacing",              "…", 90, lambda p, b: True, lambda md, *_: _spacing_fixes(md)),
]
```

*(I dettagli delle lambda vanno scritti come piccole funzioni con nome, per la
leggibilità e per i test; qui sono riassunte.)*

## Override utente (`fix_rules.json`, opzionale)

File JSON accanto al programma; se presente viene letto e sovrascrive la tabella
default (senza toccare codice). Formato minimale:

```json
{
  "disable": ["spacing"],
  "rules": [
    {"when": {"columns": {"gte": 2}}, "apply": ["reorder_columns"], "order": 5}
  ]
}
```

Il prototipo supporta solo `disable` e regole con `when` limitato a
`columns`/`backend`/`has_tables`/`has_index` e operatori `eq/gte/lte`. È il punto
d'aggancio per il **wizard** (fase successiva), che genererà queste righe dalle
risposte dell'utente.

## Migrazione di `_apply_fix` in `main.py`

Sostituire il corpo con:

```python
def _apply_fix(self, text, page_num):
    fix = self.fix_combo.currentText()
    if fix == "Nessuno":
        return text
    doc = self._get_mupdf_doc()
    if doc is None:
        return text
    profile = layout_engine.profile_page(doc[page_num])
    backend = self.backend_combo.currentText()
    mode = "auto" if fix == "Auto" else FIX_COMBO_TO_ID[fix]   # mappa manuale→id
    plan = layout_engine.plan_fixes(profile, backend, mode)
    try:
        return layout_engine.apply_plan(text, doc[page_num], profile, plan) or text
    except Exception:
        return text
```

Mappa manuale (nuova costante):

```python
FIX_COMBO_TO_ID = {
    "Riordino colonne": "reorder_columns",
    "Colonne + titolo in testa": "reorder_columns_title",
    "Spaziature": "spacing",
}
```

**Garanzia di equivalenza**: per ogni voce del combo (tranne il miglioramento docling
di `Auto`) l'output deve essere identico a oggi; lo si verifica con test di
regressione sulle pagine note.

## Euristiche nuove (minime, già derivabili)

- `has_references`: in ≥2 colonne, la maggior parte dei blocchi inizia con
  `^\d+\.` (entry numerate) e `has_small_text` è vero.
- `has_index`: `columns >= 3` e i blocchi sono righe corte con suffissi numerici
  (`\d{3,}[a-z]?$`), `has_small_text` vero.
- `columns_overlap`: riusare il check verticale già presente in
  `_page_needs_column_reorder` (sovrapposizione y tra prima e ultima colonna).

Non servono modelli AI né OCR: solo geometria dei blocchi, coerente con il resto.

## Test (`tests/test_layout_engine.py`)

Unitari (pagine sintetiche pymupdf, come già in `test_fixes.py`):

1. `profile_page` → 2 colonne: `columns == 2`, `columns_overlap` vero.
2. `profile_page` → 1 colonna: `columns == 1`.
3. `profile_page` → 4 colonne (indice): `columns == 4`, `has_index` vero.
4. `profile_page` → tabella full-width: `has_tables` vero, `columns == 1`.
5. `profile_page` → referenze 7pt: `has_small_text` e `has_references` veri.
6. `plan_fixes` auto, backend pymupdf4llm, 2 colonne → `["reorder_columns", "spacing"]`.
7. `plan_fixes` auto, backend docling, 2 colonne → `["dehyphenate", "split_glued", "spacing"]`.
8. `plan_fixes` auto, 1 colonna → `["spacing"]` (niente riordino).
9. `apply_plan` su pagina 2 colonne sintetica → ordine lettura corretto (come i test attuali).
10. Override JSON: `disable: ["spacing"]` → il piano non contiene `spacing`.

Regressione / equivalenza:

11. Le voci del combo producono lo stesso output di oggi su p.157/156/1007 (Harrison),
    p.2117/4382 (Cecil) — esteso ai golden del benchmark.
12. `Auto` su docling ora de-sillaba + ri-spezza: su p.157 l'incollato
    `region of temporal artery | treatment of both tension-type headache…` viene
    separato (il test `test_page_157_docling_glue_is_split` resta verde e viene
    rilanciato anche via `apply_plan`).

Integrazione (3 libri, già presente l'infrastruttura):

13. `benchmark/check_fixes.py` su Harrison/Cecil/Oxford: `pymupdf4llm+fix` deve
    restare 21/21 (7/7, 6/6, 8/8) — ora passa **attraverso** l'engine, non chiama più
    `_column_aware_markdown` direttamente (aggiungere una variante
    `pymupdf4llm-auto` al benchmark).

## Criteri di accettazione

- `tests/` verde: i 43 esistenti + i nuovi di `test_layout_engine.py`.
- Il combo `FIXES` mostra lo stesso comportamento di oggi (solo `Auto` migliora per
  docling), verificato da test di equivalenza.
- `Auto` è automatico ma la tabella regole è modificabile via `fix_rules.json`
  senza toccare `main.py`.
- Nessuna regressione sui 3 libri (benchmark ancorato).

## Rischi / note

- **Non fare over-engineering**: profilo con ~9 campi, 5 fix, 1 scheduler, 1 JSON
  override. Niente plugin dinamici né caricamento di codice arbitrario nel prototipo.
- **Predicati `when` solo su segnali già affidabili**: `columns`, `has_tables`,
  `columns_overlap`, `backend`, `has_index`, `has_references`. Se un'euristica nuova
  si rivela fragile, si esclude dal prototipo (non blocca l'engine).
- **Wizard e tracciamento manuale (task 3)** sono fuori da questo prototipo: qui si
  prepara solo l'aggancio (`fix_rules.json`) che li alimenterà.
