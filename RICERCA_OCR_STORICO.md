# Ricerca — Manual zoning negli OCR storici (OmniPage e FineReader)

Redatto il 2026-08-17. Documento di ricerca/documentazione (nessuna implementazione).
Informa [STUDIO_TRACCIAMENTO_MANUALE.md](STUDIO_TRACCIAMENTO_MANUALE.md) e
[PIANO_TRACCIAMENTO_MANUALE.md](PIANO_TRACCIAMENTO_MANUALE.md) (task 3).

## Perché questa ricerca

OmniPage e FineReader avevano, già decenni fa, una forma speciale di selezione
manuale delle regioni della pagina: **analizzavano la pagina, presentavano le
regioni individuate, e chiedevano all'utente di completare il lavoro con tool di
rifinitura** (testi, tabelle, immagini…). Lo scopo qui è documentare quel modello
per confrontarlo col nostro design del task 3 e decidere cosa adottare.

## Il workflow comune (il cuore)

Entrambi seguivano lo stesso ciclo a 3 fasi, identico alla nostra idea:

1. **Analisi automatica** → il programma individua le regioni e le disegna da solo
   (in OmniPage con **bordi pieni**).
2. **Rifinitura manuale** → l'utente corregge/completa con strumenti dedicati (le
   regioni disegnate a mano avevano **bordi tratteggiati**, così si distinguevano
   visivamente da quelle auto).
3. **Riconoscimento/estrazione** → il motore processa secondo le regioni e il loro
   ordine.

## Tipi di zona/area (comparativa)

| OmniPage | FineReader (ABBYY) | Mapping nostro (task 3) |
|---|---|---|
| **Text** (anche verticale sx/dx, asiatico) | **Text** | `text` |
| **Table** (con sottotipo *spreadsheet*) | **Table** | `table` |
| **Graphic** | **Picture** | `image` |
| — | **Background picture** (da NON riconoscere) | ≈ `ignore` |
| **Ignore** (parte esclusa) | **Barcode** (solo FR) | `ignore` |
| **Process** (ri-auto-zona quest'area) | — | ≈ *lascia fare all'auto* |
| **Form** (solo Pro) | — | — |

La trovata chiave di OmniPage è il concetto di **"process" vs "ignore"** e di
**background** (process/ignore): *tutta la pagina di default è "process" (fai
l'auto)*, e l'utente interviene solo disegnando "ignore" (escludi) o zone
tipizzate. È **esattamente** la nostra "modalità correzione (overlay)", con
25 anni di anticipo.

## Gli strumenti di rifinitura

- **Disegna / ridisegna** una zona (trascina i bordi per correggerla).
- **Cambia tipo** di una zona già esistente.
- **Unisci / separa** aree (FineReader: pulsanti "+/−" per collegare/staccare box
  adiacenti).
- **"On-the-fly zoning"** (OmniPage): la zona **si restringe da sola** per
  adattarsi al contenuto rilevato → lo *snapping* che noi abbiamo messo in fase 2.
- **Rifinitura tabella** (FineReader): lo strumento "bacchetta" indovina la griglia
  riga/colonna, e si possono **aggiungere/eliminare singole linee separatore**. Un
  sotto-strumento intero dedicato alle tabelle.
- **Riordina** le aree (numerandole/trascinandole).

## Ordine di lettura

FineReader ordinava le aree **top→bottom, left→right** di default, ma permetteva di
**riordinarle**; nell'engine esiste `ILayout::SortedBlocks`, cioè un ordinamento
logico separato dall'ordine fisico dei blocchi. OmniPage aveva anch'esso la voce
"Reading order". Conferma la nostra decisione: **ordine esplicito, separato dal
disegno**.

## Cosa adottiamo e cosa rimandiamo

### Già adottato (allineato alle Decisioni bloccate)

- Ciclo **auto → mostra regioni → rifinisci** (il nostro engine + tracciato).
- **Regioni tipizzate**: i nostri 5 tipi (`text | table | box | image | ignore`)
  ≈ i loro tipi. Il `ignore` copre sia il loro "ignore" sia "background picture".
- **Ordine esplicito separato dal disegno** (🔢 Sequenza = click in ordine) = il
  "reorder areas" di FineReader.
- **Override manuale = sempre** ("il manuale vince").
- **Modalità correzione (overlay)** = process/ignore background di OmniPage.

### Da anticipare (non più solo fase 2)

- **Snapping / shrink-to-fit** come comportamento di *default* quando si disegna una
  regione (OmniPage "on-the-fly"): la regione a spanne si adatta al contenuto
  auto-rilevato. Nel piano è in "Fuori scope (fase 2)"; andrebbe anticipato.
- **Sotto-tool tabella** per la regione `table` quando `find_tables` fallisce:
  "bacchetta" (auto-detect griglia) + aggiungi/elimina separatori (FineReader).

### Rimandato / non adottato

- **Tipi di testo verticale** (sx/dx/asiatico), **form**, **barcode**: fuori scope —
  i nostri libri non ne hanno bisogno; le etichette verticali nei margini le copre
  `ignore`.
- **"Background picture" come tipo a sé**: il nostro `ignore` è più generale e basta.
- **Riordino via trascinamento**: noi usiamo il click (🔢 Sequenza), più semplice da
  implementare col hit-test dei `QGraphicsRectItem`.
- **Regioni poligonali/irregolari**: OmniPage/FR le avevano ("draw irregular zone");
  noi le rimandiamo (già in "Fuori scope").

## Fonti

- OmniPage — *Zones, backgrounds and auto-zoning* (help online):
  http://omnipage.helpmax.net/en/customizing-zones/zones-backgrounds-and-auto-zoning/
- Nuance KB — *Instructions for using manual zoning with OmniPage and TextBridge*:
  https://nuance.custhelp.com/app/answers/detail/a_id/4137/~/instructions-for-using-manual-zoning-with-omnipage-and-textbridge
- ABBYY FineReader 12 help — *Adjusting Area Properties*:
  https://help.abbyy.com/en-us/finereader/12/rectext/
- ABBYY FineReader 14 help — *Editing areas*:
  https://help.abbyy.com/en-us/finereader/14/user_guide/editareas
- NYU Research Guides — *ABBYY FineReader Tutorial: Adjusting Areas / Creating Areas*:
  https://guides.nyu.edu/abbyy/adjusting-text-areas
- ABBYY Support — *How to reorder the recognized text*:
  https://support.abbyy.com/hc/en-us/articles/5177071847443-How-to-reorder-the-recognized-text
- ABBYY FineReader PDF — *Full Feature List* (brochure, tipi di area rilevati):
  https://pdf.abbyy.com/media/3denzynd/brochure-finereaderpdf-full-feature-list-en.pdf
