# Rapporto — engine adattativo completo su eco2022.pdf e david2027.pdf

Redatto il 2026-08-17. Verifica dell'**intero engine adattativo**
(profilo → piano → pipeline, non solo il fix box) con
`benchmark/check_engine.py` (backend pymupdf4llm): eco2022 10 pagine casuali,
david2027 20 pagine (seed 42, da p.200).

## Risultati

| PDF | pagine | crash | ordine multi-colonna | box | pagine con problemi |
|---|---|---|---|---|---|
| eco2022.pdf | 10 | 0 | 2/2 | 6 (ok 6) | 0 |
| david2027.pdf | 20 | 0 | 0/0 | 31 (ok 27) | 3 |

## eco2022.pdf — pulito

Layout osservati: solo **col1 (6)** e **col2 (4)** — nessuna sorpresa.
- Piano corretto: `reorder_columns+spacing` sulle 2 colonne, `spacing` su 1 colonna.
- Ordine di lettura 2/2, box 6/6, 0 problemi.

## david2027.pdf — colonna singola + box pesante

Il campione è **tutto a colonna singola** (col1 13, col1+tabella 7): la
"complicazione" di david2027 non è il multi-colonna, ma una **sequenza di box
numerati** (10.16, 10.17, … "Mechanisms of oedema", "Compartment syndrome") con
figure e tabelle occasionali.

Comportamento dell'engine (corretto):
- Su colonna singola il piano è **solo `spacing`** → l'engine **non** applica
  `reorder_columns` e lascia il rendering dei box al backend.
- pymupdf4llm rende questi box come **titoli + elenchi puntati** (buono, non
  come tabella, ma leggibile e senza perdita di struttura).

### I 3 casi segnalati (NON colpa dell'engine)

- **p.647 "MCGN nephropathy"**: falso positivo del check — la frase non esiste
  verbatim nella pagina (`page.get_text` non la contiene).
- **p.763 "Pituitary gradient absent", "2–3 screening tests"** e
  **p.1316 "4 3"**: presenti nel testo della pagina ma **assenti nel raw di
  pymupdf4llm** — testo dentro box/figura che il backend non estrae. È un
  **limite pre-esistente di pymupdf4llm**, non una regressione: l'engine su
  queste pagine applica solo `spacing` e non tocca il testo.

## Conclusione

- **eco2022**: gestito perfettamente (0 problemi).
- **david2027**: l'engine fa la cosa giusta (solo `spacing` su colonna singola);
  il rendering dei box è delegato a pymupdf4llm, che li rende come
  titoli+elenchi. Nessun crash, nessuna regressione.
- **Punto d'attenzione per il futuro**: su pagine a colonna singola con
  box/figura, pymupdf4llm può perdere **piccole porzioni di testo** dentro i
  box (2-3 frasi su 20 pagine). Se servisse recuperarle, si potrebbe estendere
  l'engine a estrarre il testo dei box anche su colonna singola (oggi
  `reorder_columns` — e quindi il rendering box — gira solo su multi-colonna).
