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

## Cosa deve tracciare l'utente: NON le colonne, ma *regioni + sequenza*

Le colonne sono un concetto *emergente*: ciò che serve davvero all'estrazione è
sapere **quali sono le regioni di contenuto** e **in che ordine leggerle**. Quindi
l'utente traccia **regioni** e ne definisce la **sequenza di lettura**.

### Tipi di regione (4 bastano)

| Tipo | Cosa traccia l'utente | Effetto sull'estrazione |
|---|---|---|
| **Testo** | un blocco/colonna/box di testo | estrai il testo della regione nell'ordine dato |
| **Tabella** | un'area tabellare | forza l'estrazione come tabella markdown |
| **Immagine/figura** | una figura o figura composita | estrai come immagine, escludi dal testo |
| **Ignora** | header/footer, numero pagina, note a margine | escludi dal testo |

### Sequenza di lettura (l'operazione chiave)

L'ordine di lettura si definisce **numerando le regioni** (nell'ordine in cui le si
disegna, oppure cliccandole in sequenza). Le colonne non vanno disegnate come
oggetto separato: **una colonna è semplicemente una (o più) regioni di testo il cui
ordine fa "colonna sinistra → colonna destra"**.

### Granularità consigliata: *grossolana* (regionale), non per-paragrafo

- **Coarse (raccomandata)**: tracciare rettangoli grandi (colonna, box, tabella,
  figura) e ordinarli. Dentro una regione l'ordine top→bottom è già corretto e lo
  fa il backend. Pochi clic, robusto.
- **Fine (solo se serve)**: tracciare singoli paragrafi. Precisa ma laboriosa; da
  riservare a casi estremi.

Motivo: l'auto sbaglia quasi sempre l'ordine **tra** regioni, non **dentro** una
colonna. Il manuale deve correggere il difetto giusto.

## Toolbar proposta (strumenti = tipi di regione + azioni)

Sopra il visualizzatore PDF (dove oggi c'è "🖱️ Seleziona zona"), una mini toolbar
con modalità-strumento:

| Strumento | Colore overlay | Azione |
|---|---|---|
| **🖱️ Seleziona zona** (esistente) | blu tratteggiato | estrazione immagine al volo (resta) |
| **📄 Regione testo** | verde | disegna → regione testo (numerata) |
| **📊 Regione tabella** | arancio | disegna → regione tabella |
| **🖼️ Regione immagine** | viola | disegna → regione immagine |
| **🚫 Regione da ignorare** | rosso | disegna → regione ignorata |
| **🔢 Sequenza** | — | clicca le regioni nell'ordine di lettura |
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
    kind: str                  # "text" | "table" | "image" | "ignore"
    bbox: tuple[float, float, float, float]   # in punti PDF
    order: int                 # sequenza di lettura (0-based)

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
  regioni testo, `_table_to_md`/`find_tables` per le tabelle, l'estrazione immagine
  esistente per le figure, salta le regioni "ignore", e concatena **nell'ordine**.
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

## Rischi

1. **Precisione del tracciamento**: la conversione scena→punti PDF deve restare
   esatta (già gestita con `_render_scale`); aggiungere un test.
2. **Estrazione dentro regioni sovrapposte**: definire una regola (l'ultima
   disegnata vince, o l'ordine) ed evitare regioni annidate nel prototipo.
3. **Sovraccarico UI**: 8 strumenti è il massimo accettabile; raggruppare in un
   menu se serve, ma partire con la toolbar piatta.
4. **Persistenza sporca**: gli override salvati devono essere facili da elencare e
   cancellare (altrimenti "pagina strana" senza capire perché).
