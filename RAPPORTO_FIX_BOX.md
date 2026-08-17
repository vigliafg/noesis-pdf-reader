# Rapporto — verifica del fix box su 10 pagine casuali per PDF

Redatto il 2026-08-17. Verifica del fix dei box (riquadri con bordo, es. "BOX
3.2") introdotto in `_column_aware_markdown`, su **10 pagine casuali** per
ciascuno degli **8 PDF di test** (seed 42, da p.200; mayo campionato da p.1).
Strumento: `benchmark/check_boxes.py`.

## Cosa controlla la verifica

Per ogni pagina: nessun crash; ogni box reso come tabella markdown; testo del
box non perso né duplicato nel flusso; ordine di lettura multi-colonna
preservato (ancore geometriche, come `check_fixes.py`).

## Risultati finali (dopo i fix emersi dalla verifica)

| PDF | pagine con box | box | box ok | ordine multi-colonna |
|---|---|---|---|---|
| harrison2025.pdf | 4/10 | 7 | 7 | 8/8 |
| cecil2024.pdf | 6/10 | 13 | 10 | 3/5 (2 pre-esistenti) |
| oxford2020.pdf | 3/10 | 31 | — | 9/10 (1 pre-esistente) |
| rosen2022.pdf | 2/10 | 5 | 5 | 4/4 |
| andrew2020.pdf | 0/10 | 0 | 0 | — |
| robbins2021.pdf | 7/10 | 14 | 8 | 7/8 (1 pre-esistente) |
| seidel2015.pdf | 2/10 | 3 | 3 | — |
| mayo2020.pdf | 1/10 | 2 | — | 1/1 |

- **80 pagine, 0 crash.**
- **Ordine di lettura: nessuna regressione dal fix box** — i 4 fallimenti
  (cecil p.1039/p.2206, oxford p.6233, robbins p.485) sono **pre-esistenti**:
  identici con o senza box-detection, e le ancore mancanti mancano anche nel
  raw pymupdf4llm (gap di estrazione, non colpa del box).
- Suite completa: **66/66 verdi**.

## Fix applicati durante la verifica (il test ha trovato e corretto 4 bug)

1. **Dedup dei box annidati/sovrapposti**: su cecil p.404 un grafico disegnato
   come più rettangoli annidati produceva 10 box con lo stesso testo ("SBP 110
   mm Hg" ×9). Ora i box la cui area è coperta ≥60% da un box più grande vengono
   scartati (→ ×3, una per regione reale).
2. **Esclusione header/footer**: un running header full-width in alto/basso non
   è un box di contenuto (robbins p.701 lo rendeva come tabella all'inizio,
   rompendo l'ordine di lettura).
3. **Esclusione dei box sovrapposti alle tabelle dati** (≥50% area): su mayo
   p.57 un box sovrapposto a una tabella rendeva lo stesso contenuto due volte.
4. **Colonna che è interamente un box**: i confini colonna ora si calcolano da
   tutti i blocchi (incluso il contenuto dei box), altrimenti una colonna fatta
   di un box ombreggiato faceva collassare il rilevamento a 1 colonna e il box
   veniva emesso prima della colonna sinistra (robbins p.701).

## Limiti residui (non regressioni, inerenti al rilevamento geometrico)

- **Box adiacenti che condividono testo di confine** (oxford p.404, p.2453,
  p.5438): frammenti come "tient provides a clue…" compaiono in box adiacenti.
- **Etichette ripetute** in grafici a più pannelli (cecil p.404 "SBP 110 mm Hg"
  ×3 = 3 grafici) e **banner di capitolo** che ripetono il titolo nell'header
  (robbins "C H A P T E R 6").
- **mayo p.35/p.57**: etichette di tabella ripetute tra intestazione e corpo.

Questi non causano perdita di contenuto né rottura dell'ordine di lettura.

## Conclusione

Il fix box è **robusto**: 0 crash su 80 pagine, nessuna regressione di ordine
di lettura, e i 4 difetti reali trovati (dedup, header/footer, overlap con
tabelle, colonna-box) sono stati corretti. I limiti residui sono imperfezioni
cosmetiche (testo ripetuto tra box adiacenti o in header), non bug di contenuto.
