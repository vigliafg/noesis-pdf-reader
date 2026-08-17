# Studio — Definitore di layout manuale (tracciamento con il mouse)

Redatto il 2026-08-16. Documento di analisi/design (nessuna implementazione oggi).
Completa [STUDIO_ENGINE_ADATTATIVO.md](STUDIO_ENGINE_ADATTATIVO.md) e
[PIANO_ENGINE_ADATTATIVO.md](PIANO_ENGINE_ADATTATIVO.md).

## Risposta alla domanda: il "manuale" serve davvero?

**Sì, ma non come meccanismo primario.** Su libri da 4.000–7.000 pagine l'auto è
obbligatorio (21/21 sui tre libri testati). Il manuale ha però **tre ruoli precisi
e ad alto valore**:

1. **Correzione / override** — per le (poche) pagine dove l'auto sbaglia, l'utente
   corregge *solo quella pagina* senza aspettare un fix generale.
2. **Authoring di regole** — l'utente traccia una volta; il tracciato viene
   convertito in una regola riutilizzabile che alimenta l'engine adattativo
   (`fix_rules.json` / wizard del task 2). È il modo più naturale per "insegnare"
   all'engine un layout nuovo.
3. **Validazione / debug** — vedere *cosa ha rilevato* l'auto (overlay) e
   confrontarlo col tracciato manuale.

Senza l'aggancio al punto 2, il manuale sarebbe solo lavoro ripetitivo; con
l'aggancio diventa il **meccanismo di crescita dell'engine**.

## Cosa può andare storto (tassonomia dei fallimenti dell'auto)

L'auto può trattare male una regione in **otto modi**, ordinati per stadio della
pipeline (rilevamento → segmentazione → classificazione → estrazione → ordinamento
→ dedup). I primi due sono gli estremi ("assente" / "fuori posto"); la maggior
parte dei fallimenti reali sta in mezzo.

| # | Caso | Cosa succede | Esempio reale |
|---|---|---|---|
| 1 | **Non rilevata** | manca del tutto dall'output | testo dentro box/figura non estratto da pymupdf4llm (david p.763, p.1316); box rosen non visto da `find_tables` |
| 2 | **Ordinata male** | c'è, ma il contenuto finisce nel flusso sbagliato | interleaving colonne (david p.27/28); header scambiato per box che sposta l'ancora (robbins p.701) |
| 3 | **Classificata male** | c'è, ma resa come il tipo sbagliato | watermark reso `#` heading (david); box reso testo piatto coi bullet incollati (rosen); header reso box/tabella (robbins) |
| 4 | **Segmentata male** | rilevata ma con estensione sbagliata | TABLE 89-1 frammentata in 5 riquadri (cecil p.1039); split colonna a 169 invece di 305 (blocco autori fuso col TOC, david p.318) |
| 5 | **Estratta male** | regione giusta, contenuto sbagliato | apici/sottoscritti persi ("10 7" invece di 10⁷, david p.948); caption incollata alla riga d'intestazione |
| 6 | **Duplicata** | emessa più volte | box sovrapposto a tabella → contenuto ×2 (mayo p.57); rettangoli annidati → "SBP 110 mm Hg" ×9 (cecil p.404) |
| 7 | **Sovrapposta ad altro** | il bbox invade un'altra regione | box che copre ~50% di una tabella → contenuto assegnato a entrambi (mayo p.57) |
| 8 | **Spezzata tra pagine** | la regione continua nella pagina dopo | tabella/box a cavallo di due pagine (segnalato in NEXT_STEPS) |

### I casi che un controllo "c'è? è in ordine?" non scova

I casi **3 (classificazione)** e **6 (duplicazione)** sono i più insidiosi: la
regione *c'è* ed è *grosso modo al posto giusto*, ma l'output è comunque sbagliato
(box appiattito a testo, contenuto ripetuto N volte). Sono proprio quelli che il
tracciato manuale risolve meglio: l'utente dice "questo è un *box*" / "questo è
*ignora*" e il motore smette di indovinare il tipo.

### Limiti del tracciamento

Il manuale non risolve tutti gli 8: i casi **1** (contenuto mai estratto dal
backend) e **5** (apici/legature perse dal backend) dipendono da *come* si estrae
il testo dentro la regione, non da *dove* la si traccia. Tracciare la zona giusta
aiuta ("guarda qui"), ma se il backend non restituisce il testo, il rettangolo non
lo fa apparire: per quelli servono migliorie all'estrazione, non al tracciamento.

Il caso **8** (regione spezzata tra pagine) non ha un tipo di regione: è un
*collegamento* tra pagine, non un rettangolo → **fuori scope (fase 2)**.

## Cosa deve tracciare l'utente: NON le colonne, ma *regioni + sequenza*

Le colonne sono un concetto *emergente*: ciò che serve davvero all'estrazione è
sapere **quali sono le regioni di contenuto** e **in che ordine leggerle**. Quindi
l'utente traccia **regioni** e ne definisce la **sequenza di lettura**.

### Tipi di regione (5 bastano)

| Tipo | Cosa traccia l'utente | Effetto sull'estrazione |
|---|---|---|
| **Testo** | un blocco/colonna di testo | estrai il testo della regione nell'ordine dato |
| **Tabella** | un'area tabellare | forza l'estrazione come tabella markdown (`_table_to_md`) |
| **Box** | un riquadro con bordo (sidebar) | tabella a una colonna + titolo, via `_detect_boxes` |
| **Immagine/figura** | una figura o figura composita | estrai come immagine, escludi dal testo |
| **Ignora** | header/footer, numero pagina, note a margine | escludi dal testo |

### Sequenza di lettura (l'operazione chiave)

L'ordine di lettura si definisce **solo in modo esplicito**, numerando le regioni
con lo strumento "🔢 Sequenza" (cliccandole nell'ordine di lettura). **Mai**
l'ordine di disegno: disegnare una regione non le assegna un posto nella sequenza,
così un errore di ordinamento si corregge ricliccando, senza ridisegnare. Le
colonne non vanno disegnate come oggetto separato: **una colonna è semplicemente
una (o più) regioni di testo il cui ordine fa "colonna sinistra → colonna destra"**.

### Granularità consigliata: *grossolana* (regionale), non per-paragrafo

- **Coarse (raccomandata)**: tracciare rettangoli grandi (colonna, box, tabella,
  figura) e ordinarli. Dentro una regione l'ordine top→bottom è già corretto e lo
  fa il backend. Pochi clic, robusto.
- **Fine (solo se serve)**: tracciare singoli paragrafi. Precisa ma laboriosa; da
  riservare a casi estremi.

Motivo: l'auto sbaglia quasi sempre l'ordine **tra** regioni, non **dentro** una
colonna. Il manuale deve correggere il difetto giusto.

### Due modalità: correzione vs ordine completo

Il tracciato interagisce con l'auto in due modi, a seconda dei tipi di regione
presenti:

- **Correzione (overlay)** — solo `ignore`/`box`/`table`/`image`, niente `text`:
  quelle zone vengono corrette, il resto della pagina resta auto. `ignore`
  esclude la bbox da rilevamento ed emissione (è la versione manuale di
  `_strip_margin_blocks`); `box`/`table`/`image` impongono il percorso di resa
  giusto lì dove l'auto non l'ha rilevato (è ciò che `_column_aware_markdown`
  già fa da solo per gli elementi che *trova*).
- **Ordine completo (sostituzione)** — compare almeno una regione `text`:
  l'utente sta definendo la sequenza di lettura, quindi il corpo viene
  sostituito: output = regioni tracciate in ordine.

Non serve un secondo motore: le correzioni si **iniettano nella pipeline auto
esistente**; solo il tipo `text` (raro, perché l'ordine auto di solito è giusto)
richiede la sostituzione completa.

## Toolbar proposta (strumenti = tipi di regione + azioni)

Sopra il visualizzatore PDF (dove oggi c'è "🖱️ Seleziona zona"), una mini toolbar
con modalità-strumento:

| Strumento | Colore overlay | Azione |
|---|---|---|
| **🖱️ Seleziona zona** (esistente) | blu tratteggiato | estrazione immagine al volo (resta) |
| **📄 Regione testo** | verde | disegna → regione testo (numerata) |
| **📊 Regione tabella** | arancio | disegna → regione tabella |
| **📦 Regione box** | ciano | disegna → regione box (sidebar) |
| **🖼️ Regione immagine** | viola | disegna → regione immagine |
| **🚫 Regione da ignorare** | rosso | disegna → regione ignorata |
| **🔢 Sequenza** | — | **l'unico** modo di definire l'ordine (clicca le regioni in sequenza) |
| **▶ Applica fix manuale** | — | ri-estrae con la definizione manuale |
| **💾 Salva regola** / **🧹 Pulisci** | — | persiste/azzera |

Ogni regione è un `QGraphicsRectItem` colorato con un **numero** (ordine). Gli
strumenti sono **mutuamente esclusivi** (uno attivo alla volta), come già per
"Seleziona zona" (`set_select_mode`).

La base tecnica esiste già: `PdfPageView` è un `QGraphicsView` con rubber-band
(`mousePress/Move/Release` + `region_selected(x0,y0,x1,y1)`), già testato e
protetto da try/except. Il task 3 la **generalizza**: da "una zona → un'immagine" a
"molte regioni tipizzate + ordine".

## Modello dati

```python
@dataclass
class Region:
    id: int
    kind: str                  # "text" | "table" | "box" | "image" | "ignore"
    bbox: tuple[float, float, float, float]   # in punti PDF
    order: int                 # sequenza di lettura (0-based; solo via "Sequenza")

@dataclass
class ManualLayout:
    pdf: str                   # nome file
    page: int                  # 1-based
    regions: list[Region]
```

Persistenza: `~/.noesis-pdf-reader/layouts/<pdf>/page_NNNN.json` (stessa cartella
dati usata per le immagini docling). Facile da leggere/modificare a mano e da
versionare.

## Integrazione con l'engine adattativo (task 2)

Il tracciato manuale diventa **un fix in più nel registro**, con priorità massima:

```python
Fix("manual_regions", "…", 0,
    when=lambda p, b: manual_override_exists(p.pdf, p.page),   # override presente
    apply=apply_manual_layout)                                 # estrai per regioni
```

- `manual_override_exists` = c'è un `ManualLayout` salvato per quella pagina.
- `apply_manual_layout(md, page, profile)` usa `page.get_textbox(clip=bbox)` per le
  regioni testo, `_table_to_md`/`find_tables` per le tabelle, `_detect_boxes` per i
  box, l'estrazione immagine esistente per le figure, salta le regioni "ignore", e
  concatena **nell'ordine**.
- Il tracciato può essere **promosso a regola** ("💾 Salva regola"): generalizza la
  definizione in una riga `fix_rules.json` (es. "questo layout → questo ordine"),
  che è il ponte col wizard del task 2.

**Nota di coerenza**: il profilo auto (`LayoutProfile`) resta l'input dei fix auto;
il manuale è un **override** che *scavalca* lo scheduler solo quando esiste.

## Cosa NON serve tracciare (per non complicare)

- **Non** le singole parole/righe: le estrae il backend dentro ogni regione.
- **Non** lo zoom/rotazione: si lavora sempre in punti PDF (conversione già fatta in
  `_on_region_selected`).
- **Non** un linguaggio di query: per il prototipo bastano rettangoli + ordine.

## Valore vs costo

| Aspetto | Valutazione |
|---|---|
| Sforzo UI | basso (generalizzare la rubber-band esistente + overlay + toolbar) |
| Sforzo estrazione per regione | basso (helper pymupdf già presenti) |
| Utilità immediata | media (correzione pagina singola) |
| Utilità strategica | **alta** (alimenta le regole dell'engine adattativo) |
| Rischio | basso (non tocca i percorsi auto esistenti) |

**Verdetto**: approccio valido e complementare all'auto. Va costruito *dopo* il
prototipo dell'engine adattativo (task 2), così il tracciato ha già un posto dove
"agganciarsi" (il registro fix + `fix_rules.json`). In ordine di implementazione:
prima l'engine, poi il tracciato manuale, infine il wizard che li unisce.

## Decisioni bloccate (brainstorm 2026-08-17)

1. **Ordine = solo esplicito.** L'unico modo di definire la sequenza è lo
   strumento "🔢 Sequenza" (numerazione per click). L'ordine di disegno **non**
   assegna mai un posto nella sequenza.
2. **"▶ Applica" bloccato** finché ci sono ≥2 regioni non numerate (nessun
   fallback all'ordine di disegno). Con una sola regione — o tutte "ignore" —
   l'ordine è irrilevante e si applica comunque.
3. **Override manuale = sempre.** Il tracciato vince su qualunque fix/modalità del
   combo, perché è l'azione più deliberata dell'utente. Per restare semplice e non
   misterioso richiede: (a) indicatore visivo di override attivo, (b) "🧹 Pulisci"
   a un clic per tornare all'auto.
4. **Correzione vs ordine completo.** Solo `ignore`/`box`/`table`/`image` → overlay
   sull'auto (le zone corrette, il resto auto). Almeno una regione `text` →
   sostituzione del corpo con le regioni in ordine.

## Rischi

1. **Precisione del tracciamento**: la conversione scena→punti PDF deve restare
   esatta (già gestita con `_render_scale`); aggiungere un test.
2. **Estrazione dentro regioni sovrapposte**: definire una regola (l'ultima
   disegnata vince, o l'ordine) ed evitare regioni annidate nel prototipo.
3. **Sovraccarico UI**: 8 strumenti è il massimo accettabile; raggruppare in un
   menu se serve, ma partire con la toolbar piatta.
4. **Persistenza sporca**: gli override salvati devono essere facili da elencare e
   cancellare (altrimenti "pagina strana" senza capire perché).
