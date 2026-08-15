# Nota — selezione a mouse di una zona ed estrazione immagine

Redatto il 2026-08-15. Idea/analisi da valutare (non implementata).

## Obiettivo (futuro)

Permettere all'utente di selezionare col mouse una zona della pagina
renderizzata (pannello sinistro) ed estrarre l'immagine/figure contenuta in
quella zona.

## Risposta alla domanda sull'API

- **PyMuPDF4LLM: no** — espone solo `to_markdown()`, nessuna estrazione
  immagine per zona.
- **PyMuPDF: sì** — già dipendenza del progetto e già aperto/cachato in
  `self._mupdf_doc` (`_get_mupdf_doc()`). API rilevanti:

| API | cosa fa |
|---|---|
| `page.get_images(full=True)` | elenca le immagini embedded della pagina |
| `page.get_image_rects(xref)` | bbox (punti PDF) di ogni immagine piazzata |
| `doc.extract_image(xref)` | byte raw dell'immagine originale |
| `page.get_image_info()` | bbox di tutte le immagini renderizzate |
| `page.get_pixmap(clip=Rect)` | renderizza una regione (anche vettoriale) |

La feature è **indipendente dal backend testo** selezionato: usa PyMuPDF
direttamente sul documento già aperto.

## Due modi di "estrarre" la zona

1. **Immagine embedded** (`get_images` + `get_image_rects` + `extract_image`):
   raster originale a piena risoluzione. Ottimo per figure = immagine singola.
2. **Render della zona** (`get_pixmap(clip=rect)`): "fotografa" ciò che
   l'utente vede; funziona anche per figure vettoriali o composte da più
   XObject.

Attenzione al problema già documentato: le figure composite (scomposte in
tanti XObject, es. pag. 1013/1015) col metodo 1 restituiscono pezzi separati.
→ Strategia: **prova l'embedded, fallback al render della zona**. Il render è
ciò che di fatto fa il `PictureItem` di Docling (per questo dà figure intere).

## Cambi architetturali richiesti

1. **Widget di selezione** sul pannello sinistro — oggi `PdfPageView` è un
   `QLabel`. Servire una rubber-band: migrare a `QGraphicsView` (rubber band +
   `mapToScene` pronti, consigliato) oppure `QLabel` + `QRubberBand` con i
   `mouse*Event`.
2. **Mappatura coordinate** (punto delicato): selezione in coordinate schermo
   → pixmap mostrato → pixmap full-res (÷ `_render_scale`, default 3.0) →
   punti PDF. Due insidie: il pixmap è centrato con letterboxing (scorporare i
   margini) e la scala cambia a ogni resize/zoom (ricalcolare in `_fit_to_view`).
3. **Nuova azione + segnale**: toggle in toolbar ("🖱️ Seleziona zona") e
   segnale dal pannello sinistro → `MainWindow` con il rettangolo in punti PDF.
4. **`_extract_image_region(page_num, rect)`**: usa `_get_mupdf_doc()` (già
   cachato) → trova l'immagine embedded che interseca il rettangolo e la
   ritaglia sull'intersezione esatta (serve `PIL`, già presente), altrimenti
   `get_pixmap(clip=rect)` ad alta risoluzione.
5. **Visualizzazione**: riusare la tab "🖼️ Immagini" / gallery esistente
   (con 💾 Salva e 📋 Copia) oppure un dialog. Zero codice nuovo lì.

## Vincoli e note

- Nessuna nuova dipendenza (PyMuPDF + PIL già presenti).
- Nessuna modifica ai backend di estrazione testo.
- Ortogonale alla decisione installazione/distribuzione (vedi
  `NEXT_STEPS-installazione.md`): non bloccata da quella.

## Criteri di accettazione (quando verrà implementata)

- Selezione visibile mentre si trascina il mouse.
- L'immagine estratta corrisponde alla zona selezionata (verifica su pag. 1013
  e 1015, figure composite: deve uscire la figura intera, non i pezzi).
- 💾 Salva / 📋 Copia funzionanti sull'immagine estratta.
- Test verdi (`python -m unittest discover -s tests -v`).
