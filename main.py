#!/usr/bin/env python3
"""Noesis PDF Reader — PyQt6 split view: rendered page (left) + extracted text (right).

Multiple text extraction backends switchable at runtime.
"""

import os
import sys
import time
from pathlib import Path

import pypdfium2 as pdfium
import pdfplumber
from pdfminer.high_level import extract_text as _pdfminer_extract
import markdown as _md_lib

_MD_EXTENSIONS = ["tables", "fenced_code", "codehilite"]

try:
    import pymupdf4llm
    _has_pymupdf4llm = True
except ImportError:
    pymupdf4llm = None  # type: ignore
    _has_pymupdf4llm = False

try:
    from docling.document_converter import DocumentConverter
    _has_docling = True
except ImportError:
    DocumentConverter = None  # type: ignore
    _has_docling = False

try:
    import pdf_oxide
    from pdf_oxide import PdfDocument as _PdfOxideDocument
    _has_pdf_oxide = True
except ImportError:
    pdf_oxide = None  # type: ignore
    _PdfOxideDocument = None  # type: ignore
    _has_pdf_oxide = False

try:
    import pdf_inspector
    _has_pdf_inspector = True
except ImportError:
    pdf_inspector = None  # type: ignore
    _has_pdf_inspector = False

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSplitter,
    QScrollArea,
    QLabel,
    QTextEdit,
    QToolBar,
    QFileDialog,
    QSpinBox,
    QPushButton,
    QComboBox,
    QMessageBox,
    QStatusBar,
    QWidget,
    QVBoxLayout,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════════════════════════════


def pdfium_page_to_pixmap(page: pdfium.PdfPage, scale: float = 3.0) -> QPixmap:
    """Render a pypdfium2 page into a QPixmap."""
    bitmap = page.render(scale=scale)
    arr = bitmap.to_numpy()
    h, w, _ = arr.shape
    img = QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(img)


def _format_table_ascii(table: list[list[str | None]], max_col_width: int = 28) -> str:
    """Format a pdfplumber table as an ASCII art table with borders."""
    if not table or not table[0]:
        return ""

    # Replace None with empty string and truncate cells
    rows = [[(str(c) if c is not None else "") for c in row] for row in table]
    ncols = len(rows[0])

    # Calculate column widths (capped)
    col_widths = [
        min(max(len(row[c]) for row in rows), max_col_width) + 2
        for c in range(ncols)
    ]

    def _sep(char: str = "─") -> str:
        return "+" + "+".join(char * w for w in col_widths) + "+"

    def _row(vals: list[str]) -> str:
        cells = []
        for c, val in enumerate(vals):
            w = col_widths[c] - 2
            if len(val) > w:
                val = val[: w - 1] + "…"
            cells.append(f" {val:<{w}} ")
        return "|" + "|".join(cells) + "|"

    lines = []
    lines.append(_sep("═"))
    lines.append(_row(rows[0]))
    lines.append(_sep("═"))
    for row in rows[1:]:
        lines.append(_row(row))
        lines.append(_sep())

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  widgets
# ═══════════════════════════════════════════════════════════════════════════════


class PdfPageView(QLabel):
    """Left panel — displays the rendered PDF page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(300)
        self.setText("Apri un PDF per iniziare")
        self.setFont(QFont("Segoe UI", 14))
        self.setStyleSheet(
            "QLabel { background: #2b2b2b; color: #888; border: none; }"
        )
        self._full_pixmap: QPixmap | None = None
        self._last_size = None

    def show_page(self, pixmap: QPixmap | None):
        """Store the full-resolution pixmap and scale it to fit the view."""
        if pixmap is None:
            self._full_pixmap = None
            self._last_size = None
            self.setText("(pagina non disponibile)")
            return
        self._full_pixmap = pixmap
        self._last_size = None
        self._fit_to_view()

    def _fit_to_view(self):
        """Scale the full-resolution pixmap to fit the current label size."""
        if self._full_pixmap is None:
            return
        view_size = self.size()
        if view_size.width() < 10 or view_size.height() < 10:
            return
        scaled = self._full_pixmap.scaled(
            view_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)
        self._last_size = (view_size.width(), view_size.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._full_pixmap is None:
            return
        new_size = self.size()
        if self._last_size == (new_size.width(), new_size.height()):
            return
        self._fit_to_view()


class TextPanel(QTextEdit):
    """Right panel — shows extracted text, optionally rendered as Markdown/HTML."""

    _HTML_CSS = """
    <style>
      body { font-family: 'Segoe UI', sans-serif; font-size: 13px;
             color: #1a1a1a; line-height: 1.7; margin: 0; }
      h1 { font-size: 1.5em; border-bottom: 2px solid #4a90d9; padding-bottom: 4px; }
      h2 { font-size: 1.3em; color: #2c5f8a; margin-top: 1em; }
      h3 { font-size: 1.15em; color: #3a7ab5; }
      strong { color: #1a3a5c; }
      em { color: #555; }
      code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px;
             font-family: 'Consolas', monospace; font-size: 0.9em; }
      pre { background: #f5f5f5; padding: 12px; border-radius: 6px;
            border: 1px solid #ddd; overflow-x: auto; }
      table { border-collapse: collapse; width: 100%; margin: 10px 0; }
      th { background: #4a90d9; color: #fff; padding: 8px 12px;
           text-align: left; font-weight: 600; }
      td { border: 1px solid #ddd; padding: 6px 12px; }
      tr:nth-child(even) { background: #f8f9fa; }
      blockquote { border-left: 4px solid #4a90d9; margin: 10px 0;
                   padding: 6px 16px; background: #f0f4f8; color: #444; }
      ul, ol { padding-left: 24px; }
      li { margin: 3px 0; }
      hr { border: none; border-top: 1px solid #ddd; margin: 16px 0; }
      a { color: #4a90d9; }
    </style>
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Segoe UI", 12))
        self.setStyleSheet(
            "QTextEdit { background: #ffffff; color: #1a1a1a; padding: 12px; }"
        )

    def show_text(self, text: str, as_markdown: bool = True):
        """Display text, optionally rendering as Markdown → HTML."""
        if as_markdown:
            html_body = _md_lib.markdown(text, extensions=_MD_EXTENSIONS)
            self.setHtml(self._HTML_CSS + html_body)
        else:
            self.setPlainText(text)

    def show_html(self, html_body: str):
        """Display raw HTML with CSS styling."""
        self.setHtml(self._HTML_CSS + html_body)


# ═══════════════════════════════════════════════════════════════════════════════
#  main window
# ═══════════════════════════════════════════════════════════════════════════════


class MainWindow(QMainWindow):
    # Available extraction backends
    BACKENDS = [
        "PyMuPDF4LLM ⚡", "Docling 🧠", "Smart (tabelle)",
        "pdf_oxide 🦀", "pdf-inspector 🔍",
        "pdfminer.six", "pypdfium2", "pdfplumber",
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Noesis PDF Reader")
        self.resize(1400, 900)
        self.setMinimumSize(800, 600)

        # State
        self._pdf_path: Path | None = None
        self._pdf_doc: pdfium.PdfDocument | None = None
        self._current_page: int = 0
        self._page_count: int = 0
        self._plumber_cache: dict = {}
        self._pdfoxide_doc = None    # cached pdf_oxide PdfDocument
        self._pdfinspector_cache = None  # cached pdf_inspector result
        self._render_scale: float = 3.0
        self._render_md: bool = True  # toggle Markdown rendering

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Toolbar
        self._build_toolbar(root_layout)

        # Splitter
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left — scroll area wrapping the page view
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.pdf_view = PdfPageView()
        self.scroll_area.setWidget(self.pdf_view)

        # Right — text panel
        self.text_panel = TextPanel()

        self.splitter.addWidget(self.scroll_area)
        self.splitter.addWidget(self.text_panel)
        self.splitter.setSizes([700, 700])

        root_layout.addWidget(self.splitter)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(
            "Pronto — apri un file PDF con 📂 Apri PDF  |  "
            "Backend testo: PyMuPDF4LLM ⚡"
        )

        # Shortcuts
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._next_page)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self._prev_page)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, self._next_page)
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, self._prev_page)
        QShortcut(QKeySequence.StandardKey.ZoomIn, self, self._zoom_in)
        QShortcut(QKeySequence.StandardKey.ZoomOut, self, self._zoom_out)
        QShortcut(
            QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_0), self, self._zoom_reset
        )
        # Shortcut to cycle backends
        QShortcut(
            QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_B), self, self._cycle_backend
        )
        QShortcut(
            QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_M), self, self._toggle_markdown
        )

        # Dark theme
        self.setStyleSheet("""
            QMainWindow { background: #2b2b2b; }
            QToolBar {
                background: #333; padding: 4px; spacing: 6px;
                border-bottom: 1px solid #444;
            }
            QToolBar QPushButton {
                background: #444; color: #eee; border: 1px solid #555;
                border-radius: 4px; padding: 6px 14px; font-size: 13px;
            }
            QToolBar QPushButton:hover { background: #555; }
            QToolBar QPushButton:pressed { background: #666; }
            QToolBar QPushButton:checked { background: #3a6bc5; color: #fff; }
            QToolBar QSpinBox {
                background: #444; color: #eee; border: 1px solid #555;
                border-radius: 4px; padding: 4px 8px; font-size: 13px;
                min-width: 60px;
            }
            QToolBar QComboBox {
                background: #444; color: #eee; border: 1px solid #555;
                border-radius: 4px; padding: 4px 8px; font-size: 13px;
                min-width: 120px;
            }
            QToolBar QComboBox:hover { background: #555; }
            QToolBar QComboBox QAbstractItemView {
                background: #444; color: #eee; selection-background-color: #666;
            }
            QToolBar QLabel { color: #ccc; font-size: 13px; }
            QStatusBar { background: #333; color: #aaa; }
        """)

        # Auto-open harrison2025.pdf if present
        default_pdf = Path("harrison2025.pdf")
        if default_pdf.exists():
            QTimer.singleShot(100, lambda: self._open_pdf(default_pdf))

    # ── toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self, parent_layout: QVBoxLayout):
        bar = QToolBar("Navigazione")
        bar.setMovable(False)
        parent_layout.addWidget(bar)

        # Apri
        btn_open = QPushButton("📂 Apri PDF")
        btn_open.clicked.connect(self._on_open)
        bar.addWidget(btn_open)

        bar.addSeparator()

        # Prev
        self.btn_prev = QPushButton("◀ Prec.")
        self.btn_prev.clicked.connect(self._prev_page)
        bar.addWidget(self.btn_prev)

        # Page spin
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setValue(1)
        self.page_spin.valueChanged.connect(self._on_spin)
        self.page_spin.setEnabled(False)
        bar.addWidget(self.page_spin)

        bar.addWidget(QLabel("di"))
        self.lbl_total = QLabel("0")
        bar.addWidget(self.lbl_total)

        # Next
        self.btn_next = QPushButton("Succ. ▶")
        self.btn_next.clicked.connect(self._next_page)
        bar.addWidget(self.btn_next)

        bar.addSeparator()

        # Zoom
        self.btn_zoom_out = QPushButton("🔍−")
        self.btn_zoom_out.setToolTip("Riduci zoom (Ctrl+-)")
        self.btn_zoom_out.clicked.connect(self._zoom_out)
        bar.addWidget(self.btn_zoom_out)

        self.zoom_label = QLabel("Scala: 3.0x")
        bar.addWidget(self.zoom_label)

        self.btn_zoom_in = QPushButton("🔍+")
        self.btn_zoom_in.setToolTip("Aumenta zoom (Ctrl++)")
        self.btn_zoom_in.clicked.connect(self._zoom_in)
        bar.addWidget(self.btn_zoom_in)

        bar.addSeparator()

        bar.addSeparator()

        # Markdown rendering toggle
        self.btn_md_toggle = QPushButton("📝 MD ✓")
        self.btn_md_toggle.setToolTip(
            "Attiva/disattiva rendering Markdown → HTML\n"
            "(Ctrl+M per toggle)"
        )
        self.btn_md_toggle.setCheckable(True)
        self.btn_md_toggle.setChecked(True)
        self.btn_md_toggle.clicked.connect(self._toggle_markdown)
        bar.addWidget(self.btn_md_toggle)

        # Text extraction backend selector
        bar.addWidget(QLabel("Testo:"))
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(self.BACKENDS)
        self.backend_combo.setToolTip(
            "Motore di estrazione testo  |  Ctrl+B per ruotare\n"
            "PyMuPDF4LLM ⚡: Markdown nativo, tabelle, ~0.8s\n"
            "Docling 🧠: AI, massima qualità, ~25s (CPU)\n"
            "Smart (tabelle): tabelle ASCII + pdfminer\n"
            "pdf_oxide 🦀: Rust, Markdown+tabelle, MIT license, ~0.5s\n"
            "pdf-inspector 🔍: classifica, Markdown, struttura PDF taggati\n"
            "pdfminer.six: buon flusso paragrafi (~2s)\n"
            "pypdfium2: ⚡ istantaneo, usa il doc già aperto\n"
            "pdfplumber: preciso con tabelle/colonne (~2s)"
        )
        self.backend_combo.currentTextChanged.connect(self._on_backend_changed)
        bar.addWidget(self.backend_combo)

    def _display_text(self, text: str):
        """Display extracted text respecting the current MD toggle."""
        self.text_panel.show_text(text, as_markdown=self._render_md)

    def _toggle_markdown(self):
        """Toggle Markdown rendering on/off and refresh display."""
        self._render_md = not self._render_md
        if self._render_md:
            self.btn_md_toggle.setText("📝 MD ✓")
        else:
            self.btn_md_toggle.setText("📝 Plain")
        # Re-render current text
        if self._pdf_doc is not None and self._page_count > 0 and self._pdf_path:
            text, elapsed = self._extract_text(self._current_page)
            backend = self.backend_combo.currentText()
            header = f"── Backend: {backend}  │  {elapsed*1000:.1f} ms  │  {len(text)} caratteri ──\n\n"
            self._display_text(header + text)

    # ── text extraction backends ──────────────────────────────────────────

    def _extract_text(self, page_num: int) -> tuple[str, float]:
        """Extract text using the selected backend. Returns (text, elapsed_secs)."""
        backend = self.backend_combo.currentText()
        t0 = time.perf_counter()

        if backend == "PyMuPDF4LLM ⚡":
            text = self._extract_pymupdf4llm(page_num)
        elif backend == "Docling 🧠":
            text = self._extract_docling(page_num)
        elif backend == "Smart (tabelle)":
            text = self._extract_smart(page_num)
        elif backend == "pdf_oxide 🦀":
            text = self._extract_pdfoxide(page_num)
        elif backend == "pdf-inspector 🔍":
            text = self._extract_pdfinspector(page_num)
        elif backend == "pypdfium2":
            text = self._extract_pdfium(page_num)
        elif backend == "pdfplumber":
            text = self._extract_plumber(page_num)
        elif backend == "pdfminer.six":
            text = self._extract_pdfminer(page_num)
        else:
            text = "(backend sconosciuto)"

        elapsed = time.perf_counter() - t0
        return text, elapsed

    # ── Nuovi backend Markdown ────────────────────────────────────────

    def _extract_pymupdf4llm(self, page_num: int) -> str:
        """PyMuPDF4LLM: blazing fast, native Markdown with tables."""
        if not _has_pymupdf4llm:
            return "(pymupdf4llm non installato — esegui: pip install pymupdf4llm)"
        try:
            md = pymupdf4llm.to_markdown(str(self._pdf_path), pages=[page_num])
            return md.strip() or "(nessun testo estraibile su questa pagina)"
        except Exception as e:
            return f"(errore pymupdf4llm: {e})"

    def _extract_docling(self, page_num: int) -> str:
        """Docling (IBM): AI-powered, best quality, slow on CPU (~25s/pag)."""
        if not _has_docling:
            return "(docling non installato — esegui: pip install docling)"
        try:
            converter = DocumentConverter()
            result = converter.convert(
                str(self._pdf_path), page_range=(page_num + 1, page_num + 1)
            )
            md = result.document.export_to_markdown()
            return md.strip() or "(nessun testo estraibile su questa pagina)"
        except Exception as e:
            return f"(errore docling: {e})"

    def _extract_pdfium(self, page_num: int) -> str:
        """Use pypdfium2's built-in text extraction (instant — doc already open)."""
        try:
            textpage = self._pdf_doc[page_num].get_textpage()
            text = textpage.get_text_range()
            return text.strip() or "(nessun testo estraibile su questa pagina)"
        except Exception:
            return "(errore pypdfium2)"

    def _extract_plumber(self, page_num: int) -> str:
        """Use pdfplumber (column/table-aware)."""
        try:
            if "pdf" not in self._plumber_cache:
                self._plumber_cache["pdf"] = pdfplumber.open(self._pdf_path)
            pdf = self._plumber_cache["pdf"]
            if page_num < len(pdf.pages):
                text = pdf.pages[page_num].extract_text()
                return text or "(nessun testo estraibile su questa pagina)"
            return ""
        except Exception:
            return "(errore pdfplumber)"

    def _extract_smart(self, page_num: int) -> str:
        """Smart: pdfplumber with ASCII-formatted tables + pdfminer.six fallback."""
        try:
            if "pdf" not in self._plumber_cache:
                self._plumber_cache["pdf"] = pdfplumber.open(self._pdf_path)
            pdf = self._plumber_cache["pdf"]
            page = pdf.pages[page_num]

            tables = page.extract_tables()
            text = page.extract_text() or ""

            if tables:
                # Format each table as ASCII art and append
                formatted_tables = []
                for i, t in enumerate(tables):
                    ft = _format_table_ascii(t)
                    if ft:
                        formatted_tables.append(f"── TABELLA {i + 1} ──\n{ft}")

                result = "\n\n".join(formatted_tables)
                result += f"\n\n{'─' * 50}\n\n" + text if text else ""
                return result
            else:
                # No tables — use pdfminer.six for better paragraph flow
                pm_text = _pdfminer_extract(
                    str(self._pdf_path), page_numbers=[page_num]
                )
                return pm_text.strip() or text or "(nessun testo estraibile su questa pagina)"
        except Exception:
            return "(errore smart)"

    def _extract_pdfoxide(self, page_num: int) -> str:
        """pdf_oxide: Rust engine, native Markdown + tables, MIT license."""
        if not _has_pdf_oxide:
            return "(pdf_oxide non installato — esegui: pip install pdf_oxide)"
        try:
            if self._pdfoxide_doc is None:
                # Suppress noisy stderr warnings during document open
                _old_stderr = sys.stderr
                sys.stderr = open(os.devnull, "w")
                try:
                    self._pdfoxide_doc = _PdfOxideDocument(str(self._pdf_path))
                finally:
                    sys.stderr.close()
                    sys.stderr = _old_stderr
            md = self._pdfoxide_doc.to_markdown(page_num, detect_headings=True)
            return md.strip() or "(nessun testo estraibile su questa pagina)"
        except Exception as e:
            return f"(errore pdf_oxide: {e})"

    def _extract_pdfinspector(self, page_num: int) -> str:
        """pdf-inspector: classify + structured Markdown, tagged PDF support.

        NOTE: extract_pages_markdown() parses the entire document on every call,
        so per-page navigation on large PDFs (>1000 pages) is inherently slow.
        Caching only helps revisiting the same page.
        """
        if not _has_pdf_inspector:
            return "(pdf-inspector non installato — esegui: pip install pdf-inspector)"
        try:
            if self._pdfinspector_cache is None:
                self._pdfinspector_cache = pdf_inspector.extract_pages_markdown(
                    str(self._pdf_path), pages=[page_num]
                )
            elif page_num not in self._pdfinspector_cache.pages:
                # Page not in cache — re-extract (re-parses entire doc, can be slow)
                self._pdfinspector_cache = pdf_inspector.extract_pages_markdown(
                    str(self._pdf_path), pages=[page_num]
                )
            page_data = self._pdfinspector_cache.pages.get(page_num)
            if page_data is not None:
                md = getattr(page_data, "markdown", None)
                if md:
                    return md.strip()
            return "(nessun testo estraibile su questa pagina)"
        except Exception as e:
            return f"(errore pdf-inspector: {e})"

    def _extract_pdfminer(self, page_num: int) -> str:
        """Use pdfminer.six (good paragraph flow)."""
        try:
            text = _pdfminer_extract(
                str(self._pdf_path), page_numbers=[page_num]
            )
            return text.strip() or "(nessun testo estraibile su questa pagina)"
        except Exception:
            return "(errore pdfminer.six)"

    # ── navigation ────────────────────────────────────────────────────────

    def _set_page(self, page_num: int):
        if self._pdf_doc is None or self._page_count == 0:
            return
        count = self._page_count
        page_num = max(0, min(page_num, count - 1))
        self._current_page = page_num

        # Render left
        page = self._pdf_doc[page_num]
        pix = pdfium_page_to_pixmap(page, scale=self._render_scale)
        self.pdf_view.show_page(pix)

        # Extract text right
        if self._pdf_path:
            text, elapsed = self._extract_text(page_num)
            backend = self.backend_combo.currentText()
            # Prepend backend + timing header
            header = f"── Backend: {backend}  │  {elapsed*1000:.1f} ms  │  {len(text)} caratteri ──\n\n"
            self._display_text(header + text)

        # Update toolbar
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page_num + 1)
        self.page_spin.blockSignals(False)

        self.status_bar.showMessage(
            f"Pagina {page_num + 1} di {count}  —  {self._pdf_path.name}"
            f"  |  Testo: {backend} ({elapsed*1000:.0f} ms)"
        )

    def _next_page(self):
        self._set_page(self._current_page + 1)

    def _prev_page(self):
        self._set_page(self._current_page - 1)

    def _on_spin(self, val: int):
        self._set_page(val - 1)

    def _on_backend_changed(self, _text: str):
        """Re-extract text for current page when backend changes."""
        self._plumber_cache.clear()
        self._pdfoxide_doc = None
        self._pdfinspector_cache = None
        if self._pdf_doc is not None and self._page_count > 0 and self._pdf_path:
            text, elapsed = self._extract_text(self._current_page)
            backend = self.backend_combo.currentText()
            header = f"── Backend: {backend}  │  {elapsed*1000:.1f} ms  │  {len(text)} caratteri ──\n\n"
            self._display_text(header + text)
            self.status_bar.showMessage(
                f"Pagina {self._current_page + 1} di {self._page_count}"
                f"  —  {self._pdf_path.name}"
                f"  |  Testo: {backend} ({elapsed*1000:.0f} ms)"
            )

    def _cycle_backend(self):
        """Cycle through backends via Ctrl+B."""
        idx = self.backend_combo.currentIndex()
        nxt = (idx + 1) % len(self.BACKENDS)
        self.backend_combo.setCurrentIndex(nxt)

    # ── zoom ───────────────────────────────────────────────────────────────

    def _zoom_in(self):
        self._render_scale = min(4.0, self._render_scale + 0.25)
        self._update_zoom()

    def _zoom_out(self):
        self._render_scale = max(0.5, self._render_scale - 0.25)
        self._update_zoom()

    def _zoom_reset(self):
        self._render_scale = 3.0
        self._update_zoom()

    def _update_zoom(self):
        self.zoom_label.setText(f"Scala: {self._render_scale:.2f}x")
        if self._pdf_doc is not None and self._page_count > 0:
            page = self._pdf_doc[self._current_page]
            pix = pdfium_page_to_pixmap(page, scale=self._render_scale)
            self.pdf_view.show_page(pix)

    # ── file open ─────────────────────────────────────────────────────────

    def _on_open(self):
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Apri PDF", "", "PDF Files (*.pdf);;All Files (*)"
        )
        if path_str:
            self._open_pdf(Path(path_str))

    def _open_pdf(self, path: Path):
        if not path.exists():
            QMessageBox.warning(self, "Errore", f"File non trovato:\n{path}")
            return

        if self._pdf_doc is not None:
            self._pdf_doc.close()

        try:
            self._pdf_doc = pdfium.PdfDocument(str(path))
            self._pdf_path = path
            self._page_count = len(self._pdf_doc)

            self._plumber_cache.clear()
            self._pdfoxide_doc = None
            self._pdfinspector_cache = None

            self.page_spin.setEnabled(True)
            self.page_spin.setMaximum(max(self._page_count, 1))
            self.lbl_total.setText(str(self._page_count))

            if self._page_count > 0:
                self._set_page(0)
            else:
                self.pdf_view.show_page(None)
                self._display_text("(PDF vuoto)")
                self.status_bar.showMessage("PDF senza pagine")
        except Exception as e:
            QMessageBox.critical(self, "Errore PDF", f"Impossibile aprire il PDF:\n{e}")
            self._pdf_doc = None
            self._pdf_path = None
            self._page_count = 0

    def closeEvent(self, event):
        if self._pdf_doc is not None:
            self._pdf_doc.close()
        self._plumber_cache.clear()
        self._pdfoxide_doc = None
        self._pdfinspector_cache = None
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════════════════════
#  entry point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Noesis PDF Reader")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
