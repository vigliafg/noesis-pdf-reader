# Traccia idee — Definitore di layout manuale (task 3)

Redatto il 2026-08-17. **Nota di tracciamento per l'agente**: consolida tutto ciò
che è emerso dal brainstorming del task 3, così da non perdere le idee quando si
passa all'implementazione. La fonte di verità resta in
[STUDIO_TRACCIAMENTO_MANUALE.md](STUDIO_TRACCIAMENTO_MANUALE.md) e
[PIANO_TRACCIAMENTO_MANUALE.md](PIANO_TRACCIAMENTO_MANUALE.md): **leggi prima
questi due**, poi questa traccia per il contesto delle decisioni e delle idee
rinviate.

## Stato

- Engine adattativo (task 2): **implementato** come modulo autonomo
  (`layout_engine.py`), attivato a richiesta ("Engine adattativo"), fix singoli
  intatti.
- Tracciato manuale (task 3): **solo progettato** (studio + piano + decisioni),
  **non ancora implementato**.
- Ricerca storica OCR (OmniPage/FineReader): documentata in
  [RICERCA_OCR_STORICO.md](RICERCA_OCR_STORICO.md).

## Decisioni bloccate (da rispettare in implementazione)

1. **Ordine = solo esplicito.** L'unico modo di assegnare la sequenza è lo
   strumento "🔢 Sequenza" (numerazione per click); l'ordine di disegno non assegna
   mai un posto. Le regioni nascono non numerate (`order: int | None = None`).
2. **"▶ Applica" bloccato** finché ci sono ≥2 regioni non numerate; con una sola
   regione (o tutte `ignore`) si applica comunque.
3. **Override manuale = sempre.** Il tracciato vince su qualunque fix/modalità del
   combo; richiede indicatore visivo di override attivo + "🧹 Pulisci" a un clic.
4. **Correzione vs ordine completo.** Solo `ignore`/`box`/`table`/`image` → overlay
   sull'auto (le zone corrette, il resto auto = process/ignore background di
   OmniPage). Almeno una regione `text` → sostituzione del corpo con le regioni in
   ordine.

## Design consolidato

- **5 tipi di regione**: `text | table | box | image | ignore` (il `box` è stato
  aggiunto durante il brainstorming: riquadro con bordo → tabella a una colonna +
  titolo, via `_detect_boxes`; non è né testo né tabella dati).
- **Tassonomia dei fallimenti** (8 casi) in STUDIO: 1 non rilevata, 2 ordinata male,
  3 classificata male, 4 segmentata male, 5 estratta male, 6 duplicata,
  7 sovrapposta, 8 spezzata tra pagine. I casi 3 e 6 sono quelli che il manuale
  risolve meglio; 1 e 5 dipendono dal backend (limite dichiarato).
- **`apply_manual_layout(md, page, layout)`**: riceve `md` per supportare la
  modalità overlay (correzione).

## Idee rinviate (da ridiscutere al momento dell'implementazione)

Non sono da implementare ora, ma **non vanno perse** — riprenderle quando si scrive
il codice del task 3:

1. **Snapping / shrink-to-fit** delle regioni al contenuto auto-rilevato, come
   comportamento di *default* del disegno (OmniPage "on-the-fly"). Oggi è in
   "Fuori scope (fase 2)"; si è discusso di anticiparlo.
2. **Sotto-tool tabella** per la regione `table` quando `find_tables` fallisce:
   "bacchetta" (auto-detect griglia) + aggiungi/elimina separatori (FineReader).
3. **Meccanismo di crescita** (il tema strategico aperto): come un tracciato diventa
   *regola* per pagine simili ("💾 Salva regola" → `fix_rules.json` → wizard). È il
   ponte tra task 3 e l'engine adattativo; accennato ma **non ancora progettato**.
4. **Caso 8 (regioni a cavallo di pagine)**: collegamento "continua a pagina dopo",
   non un rettangolo. Fuori scope (fase 2), da tenere presente.
5. **Regioni poligonali/non rettangolari** e **tipi vertical text/form/barcode**
   degli OCR storici: non adottati (i nostri libri non ne hanno bisogno).

## Istruzioni per l'agente (quando si implementa il task 3)

1. Partire da `PIANO_TRACCIAMENTO_MANUALE.md` (specifiche + test) rispettando le 4
   decisioni bloccate qui sopra.
2. Prima di codificare le parti toccate dalle "idee rinviate" (snapping, sotto-tool
   tabella), **ridiscuterle** con l'utente: era stato deciso di lasciarle in fase 2
   ma di rivalutarle all'implementazione.
3. Non dimenticare il **`box` come quinto tipo** (spesso assente nei vecchi appunti):
   è in STUDIO/PIANO, ma è un'aggiunta recente del brainstorming.
4. Il **meccanismo di crescita** (punto 3 sopra) è il prossimo tema di design da
   affrontare quando si vorrà andare oltre la correzione per-pagina.

## Documenti collegati

- [STUDIO_TRACCIAMENTO_MANUALE.md](STUDIO_TRACCIAMENTO_MANUALE.md) — design/tassonomia/decisioni
- [PIANO_TRACCIAMENTO_MANUALE.md](PIANO_TRACCIAMENTO_MANUALE.md) — specifiche implementative + test
- [RICERCA_OCR_STORICO.md](RICERCA_OCR_STORICO.md) — OmniPage/FineReader, cosa adottiamo/rimandiamo
- [PIANO_ENGINE_ADATTATIVO.md](PIANO_ENGINE_ADATTATIVO.md) / [STUDIO_ENGINE_ADATTATIVO.md](STUDIO_ENGINE_ADATTATIVO.md) — task 2 (già implementato)
