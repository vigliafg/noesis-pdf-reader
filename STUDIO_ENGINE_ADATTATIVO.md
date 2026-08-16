# Studio — Engine adattativo dei fix di layout

Redatto il 2026-08-16. Documento di analisi/design (nessuna implementazione oggi).

## Obiettivo

Sostituire l'attuale catena di `if/elif` hardcoded di `_apply_fix` con un **engine
adattativo**: rileva la struttura del layout della pagina, decide *da solo* quali
fix applicare e in quale ordine, ma resta **modificabile con facilità** da parte
dell'utente (script, database di fix, wizard, o altro). Il comportamento predefinito
deve restare automatico.

## Stato attuale (mappa del codice, già acquisita)

### Fix "atomici" già esistenti in `main.py`

| Funzione | Cosa fa | Input → output |
|---|---|---|
| `_collect_blocks(page)` | estrae blocchi testo con formattazione + de-sillabazione | page → `list[block]` |
| `_detect_column_splits(blocks, w)` | rileva N colonne (2/3/4) | blocks → `list[split]` |
| `_merged_column_intervals(...)` | merge intervalli x + scarta etichette margine | blocks → intervalli |
| `_detect_column_split(...)` | colonna singola più larga (wrapper) | blocks → `split\|None` |
| `_page_needs_column_reorder(page)` | gate euristico "servono 2 colonne affiancate" | page → `bool` |
| `_column_aware_markdown(page, move_title)` | riordino N colonne + tabelle | page → markdown |
| `_split_cross_column_paragraphs(md, page)` | ri-spezza paragrafi incollati a cavallo colonne (docling) | md → md |
| `clean_text(md)` | de-sillabazione + pulizia docling | md → md |
| `_spacing_fixes(md)` | fix cosmetici di spaziatura markdown | md → md |

### Dispatch attuale (`_apply_fix`)

Legge la stringa del combo `FIXES` (`Auto`, `Nessuno`, `Riordino colonne`,
`Colonne + titolo in testa`, `Spaziature`) e ha un ramo hardcoded per voce.
L'unico comportamento adattativo è `Auto`:

```python
if fix == "Auto":
    if backend == "Docling": return text          # salta tutto per docling
    if not _page_needs_column_reorder(page): return text
    return _column_aware_markdown(page) or text
```

### Punti deboli (perché serve l'engine)

1. **Aggiungere un fix = toccare `_apply_fix`** (e il combo `FIXES`): fragile.
2. **I segnali di layout vengono ricalcolati ad-hoc** dentro ogni fix (`find_tables`,
   `_detect_column_splits`, filtro font…) invece di essere centralizzati una sola volta.
3. **Niente composizione**: non si può esprimere "de-sillaba **poi** riordina
   colonne **poi** ri-spezza gli incollati" come pipeline configurabile.
4. **`Auto` è asimmetrico**: per docling non fa nulla, per gli altri applica solo il
   riordino. I 3 libri già testati (Harrison, Cecil, Oxford) hanno mostrato layout
   diversi (prosa 2 colonne, indice 4 colonne, referenze 7pt, tabelle full-width,
   box/figure, running header) che oggi richiedono rami/euristiche diverse.
5. **Nessun punto d'ingresso per l'utente** per aggiungere/modificare una regola.

## Segnali di layout già disponibili (da centralizzare)

Dai helper esistenti possiamo già derivare un **profilo** strutturato della pagina:

- `columns` / `column_splits` (da `_detect_column_splits`).
- regioni di tabella + conteggio tabelle full-width (`find_tables` + bbox).
- presenza di testo piccolo (<7.5pt → referenze/indici).
- blocchi full-width (separatori orizzontali: titoli, tabelle a tutta larghezza).
- `has_references` (entry numerate `1.`, `2.` … in 2 colonne, font piccolo).
- `has_index` (N≥3 colonne, righe corte, suffissi numerici tipo `, 1787`).
- backend in uso (docling vs pymupdf4llm vs altri) — condiziona i fix applicabili.

## Le tre modalità richieste (analisi pro/contro)

### A — Script semplificato editabile dall'utente

Un file (es. `fix_rules.py`) che l'utente edita per dichiarare regole, tipo:

```python
# fix_rules.py
RULES = [
    Rule(when="columns >= 2 and backend != 'docling'", apply=["reorder_columns"]),
    Rule(when="backend == 'docling'", apply=["dehyphenate", "split_glued"]),
]
```

- **Pro**: trasparente, potente, versionabile.
- **Contro**: è codice → errori sintattici, niente validazione/sandbox, serve sapere
  Python; per un utente "non sviluppatore" è ostile.

### B — Database di fix (registro dichiarativo)

Un catalogo strutturato di fix, ognuno con `id`, descrizione, **predicato di
applicabilità** e **funzione**. Le regole vivono in una tabella dati (lista/JSON),
non in rami `if`. L'engine consulta il registro.

- **Pro**: scalabile, testabile, scopribile (si può listare in UI), separa *dati* da
  *logica*.
- **Contro**: da solo non basta: serve comunque uno *scheduler* che ordina i fix
  selezionati (è il pezzo mancante, non il database in sé).

### C — Wizard di creazione fix a partire dalle risposte dell'utente

Interfaccia guidata: mostra la pagina, fa domande ("Quante colonne? C'è una tabella?
L'ordine è sbagliato? La prima colonna va letta prima della seconda?"), e dalle
risposte **genera una voce nel database** (o un profilo-ovverride per quella pagina).

- **Pro**: accessibile a chiunque, produce regole senza scrivere codice; ottimo per
  trasformare il "tracciamento manuale" (task 3) in regole riutilizzabili.
- **Contro**: laborioso, non scala da solo su libri da migliaia di pagine; va usato
  come *strumento di authoring* che alimenta A/B, non come meccanismo principale.

### D — Altre modalità considerate

- **Configurazione dichiarativa (YAML/JSON)** invece che script Python: sicura e
  validabile, ma meno espressiva; buona come *overlay* sopra B.
- **DSL interno limitato** (mini-linguaggio `when:` con operatori `columns >= 2`,
  `backend == "docling"`, `and/or/not`): è la via di mezzo tra A e un JSON.
- **Learning/tuning automatico** (classificare il layout con un modello): fuori
  portata per un tool locale CPU-only, e meno interpretabile; da non fare ora.

## Architettura raccomandata (ibrido B + overlay A/JSON + wizard come authoring)

**"Profilo → Piano → Pipeline"**, con un **database di fix** come fonte di verità e
un **file di override utente** per la flessibilità.

```
                        ┌────────────────────────────┐
   page (pymupdf) ────▶ │  profiler: LayoutProfile    │   puro, testabile
                        └────────────┬───────────────┘
                                     │ profile
                        ┌────────────▼───────────────┐
   backend ───────────▶ │  scheduler: plan_fixes()   │  legge il DATABASE regole
   mode (Auto/manuale)─▶│  (predicati `when`)        │  + override utente
                        └────────────┬───────────────┘
                                     │ [Fix, Fix, …]
                        ┌────────────▼───────────────┐
   markdown ──────────▶ │  pipeline: applica in ordine│  ogni Fix è una funzione
                        └────────────┬───────────────┘
                                     ▼
                                markdown corretto
```

### Componenti

1. **`LayoutProfile`** (dataclass immutabile) — il profiler ispeziona la pagina
   **una sola volta** e produce tutti i segnali (colonne, tabelle, font, referenze,
   indice, separatori…). È puro e deterministico → facilmente testabile con pagine
   sintetiche.

2. **Registro dei fix (`Fix`)**: `id`, `description`, `order`, `when(profile,
   backend)`, `apply(...)`. I fix built-in sono già le funzioni esistenti, solo
   incapsulate. Il registro è una lista/dict **dati**, non rami di codice.

3. **Scheduler (`plan_fixes`)**: dato `profile` + `backend` + `mode`, restituisce la
   sequenza ordinata di fix i cui predicati sono veri. È l'unico punto dove si
   decide "cosa applicare"; le regole stanno in una **tabella dichiarativa**.

4. **Override utente** (`fix_rules.json` o `fix_rules.py`): l'utente può
   aggiungere/disattivare/riordinare regole senza toccare `main.py`. Se è JSON è
   validabile; se è Python è più espressivo (decisione al momento dell'implementazione:
   partire da JSON, aggiungere lo script se serve).

5. **Wizard (fase successiva)**: genera righe nel database/override a partire dalle
   risposte dell'utente (collegato naturalmente al tracciamento manuale del task 3).

### Perché è "automatico ma flessibile"

- **Automatico**: il profiler + il database regole default producono da soli il piano
  giusto (es. `Auto`), senza input utente.
- **Flessibile**: la logica decisionale è **dati** (tabella regole), non codice;
  l'utente la modifica via override JSON o wizard, e ogni nuovo layout = nuovo
  profilo/campo + nuova riga, senza toccare lo scheduler.

## Tabella regole di default proposta per `Auto` (esempio)

| priorità | quando | fix |
|---|---|---|
| 10 | `backend == docling` | `dehyphenate` (`clean_text`) |
| 20 | `backend == docling and columns >= 2` | `split_glued` |
| 30 | `backend != docling and columns >= 2 and columns_overlap` | `reorder_columns` |
| 40 | sempre | `spacing` |

*(Nota: rispetto a oggi, `Auto` su docling passerebbe da "non fa nulla" a
"de-sillaba + ri-spezza gli incollati", che è un miglioramento già dimostrato
dall'analisi su p.157.)*

## Scalabilità

- **Nuovo layout** (es. "pagina con box laterale", "tre colonne") → aggiungi un campo
  a `LayoutProfile`, un fix al registro, una riga alla tabella. Lo scheduler non cambia.
- **Nuovo backend** → i predicati `when` ricevono già `backend` come parametro.
- **Regressione controllata** → ogni fix è testato in isolamento; lo scheduler è
  testato con profili sintetici; i 3 libri reali fanno da golden set (già presente in
  `benchmark/check_fixes.py`).

## Rischi

1. **Over-engineering**: il prototipo deve restare piccolo (profilo con 6-8 campi,
   4-5 fix, una tabella regole), non un framework generale.
2. **Regressione del comportamento attuale**: i nomi del combo `FIXES` devono
   produrre lo stesso output di oggi; migrazione con test di equivalenza.
3. **Predicati fragili**: `when` deve restare su segnali già affidabili
   (`columns`, `has_tables`, `backend`), non su euristiche nuove non validate.
4. **Override utente insicuro** (se Python): usare JSON per il prototipo, e al più
   uno script opzionale chiaramente "usa a tuo rischio".
