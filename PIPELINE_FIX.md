# Pipeline del fix di layout (semigrafica)

Rappresentazione logica di come agisce il fix `_column_aware_markdown`
(il "riordino colonne" testato funzionante su Harrison, Cecil e Oxford).

## La pipeline principale (da pagina → markdown)

```
                ┌───────────────────────────┐
                │   pagina PDF (pymupdf)     │
                └──────────────┬────────────┘
                               ▼
        ┌──────────────────────────────────────────────┐
        │ 1. _collect_blocks(page)                     │
        │    estrae blocchi testo + formattazione       │
        │    (bold/italic) + de-sillabazione a fine riga│
        │    "un- | common"  →  "uncommon"              │
        └──────────────┬───────────────────────────────┘
                       ▼
        ┌──────────────────────────────────────────────┐
        │ 2. find_tables()  →  regioni tabella          │
        │    • celle → _table_to_md  (markdown)         │
        │    • celle RIMOSSE dal flusso testo           │
        └──────────────┬───────────────────────────────┘
                       ▼
        ┌──────────────────────────────────────────────┐
        │ 3. classifica i blocchi rimasti               │
        │    • full-width  (≥60% pagina) → SEPARATORI   │
        │    • body        (25..60%)     → colonne      │
        │    • <25pt                     → scartato     │
        └──────────────┬───────────────────────────────┘
                       ▼
        ┌──────────────────────────────────────────────┐
        │ 4. _detect_column_splits(body)                │
        │    merge intervalli x → gap ≥ 8pt → split     │
        │    (N colonne: 2 / 3 / 4; margini esclusi)    │
        └──────────────┬───────────────────────────────┘
                       ▼
        ┌──────────────────────────────────────────────┐
        │ 5. assegna ogni blocco alla sua colonna       │
        │    (centro x vs splits)                       │
        │    + ordina ogni colonna top→bottom (y0, x0)  │
        └──────────────┬───────────────────────────────┘
                       ▼
        ┌──────────────────────────────────────────────┐
        │ 6. emissione per banda (delimitata da sep.)   │
        │    col1↓ → col2↓ → … → colN↓ → separatore     │
        │    ogni paragrafo emesso UNA sola volta       │
        └──────────────┬───────────────────────────────┘
                       ▼
                ┌───────────────────────────┐
                │  markdown in ordine lettura│
                └───────────────────────────┘
```

## Il gate "Auto" (decisione prima della pipeline)

```
fix "Auto"
    │
    ▼
 backend == "Docling 🧠" ? ──sì──▶ testo INVARIATO
    │ no                            (oggi; col piano: de-hyphen + split_glued)
    ▼
 _page_needs_column_reorder(page) ?
    │ no ──────────────────▶ testo INVARIATO
    ▼ sì
 _column_aware_markdown(page)  ──▶ markdown riordinato
```

## Esempio concreto (pagina a 2 colonne)

```
            pagina                          lettura corretta
┌──────────────────┬──────────────────┐
│ A1  The innerv…  │ B1  treatment…   │      A1 → A2 → A3 → B1 → B2 → B3
│ A2  CLINICAL…    │ B2  Underlying…  │
│ A3  TABLE 17-2   │ B3  SECONDARY…   │      (colonna sinistra tutta,
└──────────────────┴──────────────────┘       poi colonna destra)

senza fix (pymupdf4llm raw):  A1 → B1 → A2 → B2 → A3 → B3   ✗ intrecciato
con il fix:                    A1 → A2 → A3 → B1 → B2 → B3   ✓
```

## Meccanismo chiave

I **separatori** (blocchi full-width e tabelle a tutta larghezza) tagliano la pagina
in *bande*: dentro ogni banda si emette prima tutta la colonna di sinistra
(top→bottom), poi quella di destra, poi il separatore. È questo che garantisce
l'ordine di lettura e impedisce che un paragrafo venga perso o emesso due volte.
