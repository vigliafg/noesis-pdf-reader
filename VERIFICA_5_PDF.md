# Verifica — i 5 fix esistenti coprono i layout dei 5 nuovi PDF?

Redatto il 2026-08-17. Risultato della verifica chiesta: *"se ti dessi altri 5 PDF,
potremmo verificare che non ci sia bisogno di altri e nuovi fix?"*

## PDF verificati

| PDF | pagine | note |
|---|---|---|
| rosen2022.pdf | 3050 | libro completo |
| andrew2020.pdf | 1065 | libro completo |
| robbins2021.pdf | 1377 | libro completo |
| seidel2015.pdf | 339 | libro completo |
| mayo2020.pdf | 163 | **campione estratto** (2,1 MB): ha solo 163 pagine, campionato da p.1 |

## Metodo

1. **Survey casuale** (`benchmark/survey_layouts.py`): 10 pagine/PDF da p.200
   (seed 7). Per ogni pagina: segnali di profilo + estrazione pymupdf4llm (raw)
   e `_column_aware_markdown` (fix) + verifica ordine di lettura con ancore
   geometriche (logica di `check_fixes.py`).
2. **Survey di coda**: 10 pagine/PDF dalle ultime 15 (dove stanno indici/referenze).
3. **Scansione completa** (`benchmark/scan_layouts.py`, solo geometria, senza
   `find_tables`): tutte le ~6000 pagine, per trovare i layout "interessanti".
4. **Verifica mirata** sulle pagine a ≥3 colonne trovate dalla scansione.

## Risultati

### Ordine di lettura (il problema che il fix risolve)

| campione | pagine multi-colonna | raw corretto | fix corretto |
|---|---|---|---|
| casuale (5 PDF) | 18 | 4/18 | **18/18** |
| coda (4 PDF) | 15 | ~3/15 | **15/15** |
| col3+ mirate (rosen TOC, robbins indice, mayo indice) | 16 | 8/16 | **16/16** |

Il fix `_column_aware_markdown` (id `reorder_columns`) gestisce correttamente
**tutte** le pagine multi-colonna dei nuovi PDF, incluse quelle a **3 colonne**
(TOC di rosen p.6-13, indici di robbins p.1330-1362 e mayo p.159-162).

### Layout osservati (scansione completa)

| PDF | col1 | col2 | col3+ | testo piccolo |
|---|---|---|---|---|
| rosen2022.pdf | 1204 | 1831 | 15 (TOC p.6-13) | 69 |
| andrew2020.pdf | 927 | 135 | 3 (pagine quasi vuote) | 7 |
| robbins2021.pdf | 423 | 950 | 4 (indice p.1330-1362) | 130 |
| seidel2015.pdf | 312 | 27 | 0 | 24 |
| mayo2020.pdf | 95 | 64 | 4 (indice p.159-162) | 30 |

**Nessun layout nuovo** rispetto a Harrison/Cecil/Oxford: prosa 2 colonne, TOC e
indice a 3 colonne, tabelle (rare nel campione), testo piccolo (note/referenze
dentro i blocchi body, non un layout separato da trattare).

### Anomalie esaminate e spiegate

- **seidel p.238** (raw 623 → fix 169 char): pagina di figura (dermatomi). Il
  raw include le etichette della figura (C2, C3, S5… = rumore); il fix le scarta
  e tiene la caption. **Non è una regressione**, è il comportamento voluto.
- **robbins p.1365 / seidel p.326** (col1, perdita di caratteri): artefatto della
  survey, che applica il fix a *tutte* le pagine. In `Auto` il gate
  `_page_needs_column_reorder` esclude le pagine col1, quindi il fix non viene
  applicato. Nessun problema reale.
- **andrew p.244/990/1030** (col3+ ma quasi vuote): falsi positivi del
  rilevamento colonne su pagine con pochi blocchi sparsi; non rilevanti.

## Scoperta importante: l'euristica `has_index` non rileva gli indici reali

Gli **indici veri** (robbins p.1330, mayo p.159 "INDEX") esistono nei nuovi PDF e
il fix li gestisce già bene. Ma l'euristica `has_index` proposta nel piano
(*"righe corte con suffissi numerici a fine riga"*) **non scatta mai** (idx=0 in
tutti i PDF) perché:

- pymupdf **fonde più voci d'indice in un unico blocco/riga**
  (`"D dabigatran, 86, 87, 90, 137 daptomycin, 56 daunorubicin, 4…"`);
- il blocco non termina con un suffisso numerico (finisce a metà voce) e non è
  "corto" (decine di parole).

Se vogliamo il segnale `has_index` nel `LayoutProfile` va **ridefinito**, ad es.:
densità di numeri-pagina nel testo (`\d{1,3}(?:–\d{1,3})?[a-z]?` ovunque nel
blocco, non a fine riga), oppure presenza dell'intestazione "INDEX"/"INDICE" +
frazione di blocchi con pattern numerico. `has_references` resta **non verificato**
(nessuna pagina referenze nei 5 PDF).

## Conclusione

**I 5 fix esistenti sono sufficienti per i layout presenti nei 5 nuovi PDF** —
verificato su 49 pagine multi-colonna (ordine di lettura 49/49 con il fix). Non
serve alcun fix nuovo per questi libri.

Implicazioni per l'engine adattativo (task 2):

1. Il prototipo può procedere con i 5 fix esistenti; i `when` di default usano
   solo `columns`/`backend`/`columns_overlap` (già affidabili).
2. Il campo `has_index` del profilo va corretto come sopra **se** lo vogliamo nel
   profilo; non è bloccante per il prototipo (nessun fix lo consuma di default).
3. `has_references` resta un segnale non ancora validato sui dati reali: tenerlo
   nel profilo ma non usarlo nei `when` di default (coerente col piano).
4. Gli indici a 3 colonne non richiedono un fix dedicato: `reorder_columns` li
   copre già (16/16).

## Strumenti aggiunti

- `benchmark/survey_layouts.py` — profilo + estrazione + ordine di lettura su un
  campione di pagine.
- `benchmark/scan_layouts.py` — scansione completa veloce dei layout (senza
  `find_tables`, ~100s su rosen 3050 pagine).
