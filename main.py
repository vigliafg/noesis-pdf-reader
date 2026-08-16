#!/usr/bin/env python3
"""Noesis PDF Reader — PyQt6 split view: rendered page (left) + extracted text (right).

Multiple text extraction backends switchable at runtime.
"""

import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pypdfium2 as pdfium
import pdfplumber
from pdfminer.high_level import extract_text as _pdfminer_extract
import markdown as _md_lib

_MD_EXTENSIONS = ["tables", "fenced_code", "codehilite"]

# Shown in place of the text while Docling extracts a page in background.
# Must never be translated or cached as a real page translation.
_EXTRACTING_PLACEHOLDER = "⏳ Estrazione in corso…"

try:
    import pymupdf4llm
    _has_pymupdf4llm = True
except ImportError:
    pymupdf4llm = None  # type: ignore
    _has_pymupdf4llm = False

try:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling_core.types.doc import PictureItem
    _has_docling = True
except ImportError:
    DocumentConverter = None  # type: ignore
    PdfFormatOption = None  # type: ignore
    InputFormat = None  # type: ignore
    PdfPipelineOptions = None  # type: ignore
    PictureItem = None  # type: ignore
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
    import pymupdf
    _has_pymupdf = True
except ImportError:
    pymupdf = None  # type: ignore
    _has_pymupdf = False

try:
    from PyQt6.QtPdf import QPdfDocument
    _has_qtpdf = True
except ImportError:
    QPdfDocument = None  # type: ignore
    _has_qtpdf = False

from PyQt6.QtCore import (
    Qt, QThread, QTimer, pyqtSignal, QSize, QUrl, QStandardPaths, QRectF,
)
from PyQt6.QtGui import (
    QImage, QPixmap, QFont, QKeySequence, QShortcut,
    QPen, QBrush, QColor, QPainter,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
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
    QDialog,
    QMessageBox,
    QStatusBar,
    QWidget,
    QVBoxLayout,
    QDockWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsTextItem,
    QFrame,
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


def clean_text(text: str) -> str:
    """Remove end-of-line hyphenation: "com-\npany" -> "company"."""
    return re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)


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


def _page_embedded_images(doc, page_num: int) -> list[tuple[bytes, str]]:
    """Return ``[(raw_bytes, ext), ...]`` for a page's embedded raster images."""
    out: list[tuple[bytes, str]] = []
    if not _has_pymupdf:
        return out
    try:
        page = doc[page_num]
    except Exception:
        return out
    seen: set[int] = set()
    for img in page.get_images(full=True):
        xref = img[0]
        if xref in seen:
            continue
        seen.add(xref)
        try:
            info = doc.extract_image(xref)
        except Exception:
            continue
        data = info.get("image")
        if data:
            out.append((data, (info.get("ext") or "png").lower()))
    return out


def _region_image(
    doc, page_num: int, clip, zoom: float
) -> tuple[bytes, str] | None:
    """Extract the image under a PDF-points rect as ``(bytes, ext)``.

    Prefers the original embedded raster when one fully covers the selection;
    otherwise renders the region (whole figure, also for composite/vector
    figures). Returns None when nothing can be extracted.
    """
    if not _has_pymupdf:
        return None
    try:
        page = doc[page_num]
        rect = pymupdf.Rect(clip)
        if rect.width < 1.0 or rect.height < 1.0:
            return None

        # 1) embedded image whose placement is fully inside the selection
        for img in page.get_images(full=True):
            xref = img[0]
            for r in page.get_image_rects(xref):
                if (
                    r.x0 >= rect.x0 and r.y0 >= rect.y0
                    and r.x1 <= rect.x1 and r.y1 <= rect.y1
                ):
                    info = doc.extract_image(xref)
                    data = info.get("image")
                    if data:
                        return data, (info.get("ext") or "png").lower()

        # 2) fallback: render the selected region at high resolution
        pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(zoom, zoom))
        return pix.tobytes("png"), "png"
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  layout fixes (generic corrections for two-column / chapter-open pages)
# ═══════════════════════════════════════════════════════════════════════════════


def _collect_blocks(page) -> list[dict]:
    """Extract text blocks (with per-span formatting) from a pymupdf page."""
    blocks: list[dict] = []
    for blk in page.get_text("dict")["blocks"]:
        if blk.get("type") != 0:
            continue
        lines: list[list[dict]] = []
        max_size = 0.0
        for line in blk["lines"]:
            spans: list[dict] = []
            for s in line["spans"]:
                t = s["text"]
                if not t.strip():
                    continue
                spans.append(
                    {
                        "text": t,
                        "size": s["size"],
                        "bold": bool(s["flags"] & 16),
                        "italic": bool(s["flags"] & 2),
                    }
                )
                max_size = max(max_size, s["size"])
            if spans:
                lines.append(spans)
        # De-hyphenate words split across a line break ("un-" + "common" →
        # "uncommon"). Only when the next line starts lowercase, so real
        # hyphenated compounds at line ends are left alone.
        if lines:
            fused: list[list[dict]] = [lines[0]]
            for nxt in lines[1:]:
                cur = fused[-1]
                if (
                    cur and nxt
                    and cur[-1]["text"].endswith("-")
                    and len(cur[-1]["text"]) > 1
                    and nxt[0]["text"][:1].islower()
                ):
                    cur[-1]["text"] = cur[-1]["text"][:-1] + nxt[0]["text"]
                    nxt = nxt[1:]
                if nxt:
                    fused.append(nxt)
            lines = fused
        if not lines:
            continue
        x0, y0, x1, y1 = blk["bbox"]
        blocks.append(
            {
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "max_size": max_size, "lines": lines,
            }
        )
    return blocks


def _merged_column_intervals(blocks: list[dict], page_width: float) -> list[list[float]]:
    """Merge narrow blocks into per-column x-intervals, dropping margin labels.

    Margin labels, page numbers and vertical side labels are narrow (<40pt)
    and must not be mistaken for a text column.
    """
    col = [b for b in blocks if (b["x1"] - b["x0"]) < 0.6 * page_width]
    if len(col) < 4:
        return []
    intervals = sorted((b["x0"], b["x1"]) for b in col)
    merged = [list(intervals[0])]
    for x0, x1 in intervals[1:]:
        if x0 <= merged[-1][1] + 3:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    # Drop narrow labels that sit in the outer page margins (page numbers,
    # vertical side labels); narrow lines in the middle are real content.
    return [
        m for m in merged
        if not ((m[1] - m[0]) < 40 and (m[0] < 30 or m[1] > page_width - 30))
    ]


def _detect_column_splits(blocks: list[dict], page_width: float) -> list[float]:
    """Return the x boundaries between text columns (N-1 splits for N columns).

    Handles any number of columns (two-column prose, three/four-column
    indexes). Every gap between merged column intervals that is comparable to
    the widest gap is a column boundary; narrow indentation/margin gaps are
    dropped, so they never split a column.
    """
    merged = _merged_column_intervals(blocks, page_width)
    if len(merged) < 2:
        return []
    gaps = [merged[i + 1][0] - merged[i][1] for i in range(len(merged) - 1)]
    widest = max(gaps)
    return [
        (merged[i][1] + merged[i + 1][0]) / 2
        for i, gap in enumerate(gaps)
        if gap >= 8 and gap >= 0.55 * widest
    ]


def _detect_column_split(blocks: list[dict], page_width: float):
    """Return the widest column boundary, or None if single-column."""
    merged = _merged_column_intervals(blocks, page_width)
    if len(merged) < 2:
        return None
    best_gap = 0.0
    split = page_width / 2
    for i in range(len(merged) - 1):
        gap = merged[i + 1][0] - merged[i][1]
        if gap > best_gap:
            best_gap = gap
            split = (merged[i][1] + merged[i + 1][0]) / 2
    return split if best_gap >= 8 else None


def _block_to_md(block: dict, as_column: bool) -> str:
    """Render a block to markdown, marking headings when it's a column block."""
    lines: list[str] = []
    for line in block["lines"]:
        parts: list[str] = []
        for s in line:
            t = s["text"]
            if s["bold"] and s["italic"]:
                parts.append(f"***{t}***")
            elif s["bold"]:
                parts.append(f"**{t}**")
            elif s["italic"]:
                parts.append(f"*{t}*")
            else:
                parts.append(t)
        lines.append("".join(parts).strip())

    if not as_column:
        return " ".join(lines)

    size = block["max_size"]
    level = 0
    if size >= 14:
        level = 1
    elif size >= 12:
        level = 2
    elif size >= 9.8 and any(s["bold"] for l in block["lines"] for s in l):
        level = 3

    if level == 0:
        return " ".join(lines)
    if level == 1:
        return "# " + " ".join(lines)

    heading = lines[0]
    body = " ".join(lines[1:])
    if body:
        return f"{'#' * level} {heading}\n\n{body}"
    return f"{'#' * level} {heading}"


def _table_to_md(page, table) -> str:
    """Render a pymupdf table as a markdown table using clean per-cell text."""
    cells = sorted(table.cells, key=lambda c: (c[1], c[0]))  # by y0 then x0
    if not cells:
        return ""

    def _cell_text(cell) -> str:
        x0, y0, x1, y1 = cell
        clip = (x0 + 1.5, y0 + 1.5, max(x0 + 1.5, x1 - 1.5), max(y0 + 1.5, y1 - 1.5))
        return " ".join(page.get_textbox(clip).split()).strip()

    # Group cells into rows by their top y-coordinate.
    rows: list[tuple[float, list[tuple[float, str]]]] = []
    for cell in cells:
        txt = _cell_text(cell)
        if not txt:
            continue
        y0 = cell[1]
        if rows and abs(rows[-1][0] - y0) < 5.0:
            rows[-1][1].append((cell[0], txt))
        else:
            rows.append((y0, [(cell[0], txt)]))
    for _, row in rows:
        row.sort(key=lambda c: c[0])

    if not rows:
        return ""

    out: list[str] = []
    # A leading single-cell row is treated as the table caption.
    if len(rows[0][1]) == 1:
        caption = rows[0][1][0][1].replace("\n", " ")
        out.append(f"**{caption}**")
        out.append("")  # blank line so the markdown renderer sees the table
        rows = rows[1:]

    if not rows:
        return "\n".join(out)

    header = [txt.replace("\n", " ") for _, txt in rows[0][1]]
    ncols = len(header)
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join("---" for _ in header) + " |")
    for _, row in rows[1:]:
        cell_md = [txt.replace("\n", "<br>") for _, txt in row]
        cell_md = (cell_md + [""] * ncols)[:ncols]
        out.append("| " + " | ".join(cell_md) + " |")
    return "\n".join(out)


def _column_aware_markdown(page, move_title: bool = False) -> str:
    """Reconstruct a page in correct reading order.

    Body text is decomposed into consecutive paragraphs, column by column:
    within each band the left column is emitted top-to-bottom and then the
    right column top-to-bottom. Only elements that span the full page width
    (titles, full-width tables) act as horizontal separators between bands;
    single-column tables stay inside their own column, so they never split
    the other column. Every body paragraph is emitted exactly once.
    """
    page_width = page.rect.width

    # Detect data tables (rendered as markdown) and their bboxes.
    table_regions: list[tuple] = []
    table_items: list[dict] = []  # {y0, x0, x1, md}
    try:
        tabs = page.find_tables()
    except Exception:
        tabs = None
    if tabs:
        for t in tabs.tables:
            if t.row_count <= 1 and t.col_count <= 2:
                continue  # likely a chapter-title block, not a data table
            bbox = tuple(t.bbox)
            table_regions.append(bbox)
            md = _table_to_md(page, t)
            if md:
                table_items.append(
                    {"y0": bbox[1], "x0": bbox[0], "x1": bbox[2], "md": md}
                )

    def _inside(b: dict, r: tuple) -> bool:
        return (
            b["x0"] >= r[0] - 2 and b["x1"] <= r[2] + 2
            and b["y0"] >= r[1] - 2 and b["y1"] <= r[3] + 2
        )

    blocks = [b for b in _collect_blocks(page) if b["max_size"] >= 6.5]

    full_width: list[dict] = []  # spans the page → separator
    body: list[dict] = []        # column paragraphs (outside tables)
    for b in blocks:
        if any(_inside(b, r) for r in table_regions):
            continue  # covered by the markdown table
        w = b["x1"] - b["x0"]
        if w >= 0.6 * page_width:
            full_width.append(b)
        elif w >= 25:
            body.append(b)

    # Robust column boundaries, computed from body paragraphs only.
    # Handles two-column prose as well as three/four-column indexes.
    splits = _detect_column_splits(body, page_width)

    def _col_of(x: float) -> int:
        return sum(1 for s in splits if x > s)

    # Move chapter title(s) out of the column flow when requested.
    titles: list[dict] = []
    if move_title:
        first_split = splits[0] if splits else page_width + 1
        titles = [b for b in body if b["max_size"] >= 14 and b["x0"] < first_split]
        body = [b for b in body if b not in titles]
        titles.sort(key=lambda b: b["max_size"])

    # Full-width separators: full-width blocks + full-width tables only.
    separators: list[tuple[float, str]] = []
    for b in full_width:
        md = _block_to_md(b, as_column=False)
        if md:
            separators.append((b["y0"], md))
    for ti in table_items:
        if (ti["x1"] - ti["x0"]) >= 0.6 * page_width:
            separators.append((ti["y0"], ti["md"]))
    separators.sort(key=lambda s: s[0])

    # Column paragraphs, decomposed top-to-bottom: (y0, x0, md).
    n_cols = len(splits) + 1
    col_items: list[list[tuple[float, float, str]]] = [[] for _ in range(n_cols)]
    for b in body:
        md = _block_to_md(b, as_column=True)
        if not md:
            continue
        item = (b["y0"], b["x0"], md)
        col_items[_col_of((b["x0"] + b["x1"]) / 2)].append(item)
    # Single-column tables belong to their column, at their y position.
    for ti in table_items:
        if (ti["x1"] - ti["x0"]) >= 0.6 * page_width:
            continue
        mid = (ti["x0"] + ti["x1"]) / 2
        item = (ti["y0"], ti["x0"], ti["md"])
        col_items[_col_of(mid)].append(item)
    for items in col_items:
        items.sort(key=lambda it: (it[0], it[1]))

    out: list[str] = []
    for t in titles:
        out.append(_block_to_md(t, as_column=True))

    sep_marks = [y0 for y0, _ in separators]

    def _band(y0: float) -> int:
        return sum(1 for sy in sep_marks if y0 >= sy)

    n_seps = len(separators)
    for band_idx in range(n_seps + 1):
        for items in col_items:
            out.extend(md for y0, _x, md in items if _band(y0) == band_idx)
        if band_idx < n_seps:
            out.append(separators[band_idx][1])

    return "\n\n".join(out)


def _page_needs_column_reorder(page) -> bool:
    """Heuristic: does this page need the two-column reorder fix?

    True only when a clear column split exists, both columns are populated
    (≥2 blocks each) and they run side by side (their blocks overlap
    vertically) — the exact condition where a line-by-line extraction
    interleaves the two columns.
    """
    # Data tables are rendered separately; exclude their cells from the split
    # detection (same rule as _column_aware_markdown).
    table_regions: list[tuple] = []
    try:
        tabs = page.find_tables()
    except Exception:
        tabs = None
    if tabs:
        for t in tabs.tables:
            if t.row_count <= 1 and t.col_count <= 2:
                continue  # likely a chapter-title block, not a data table
            table_regions.append(tuple(t.bbox))

    def _inside(b: dict, r: tuple) -> bool:
        return (
            b["x0"] >= r[0] - 2 and b["x1"] <= r[2] + 2
            and b["y0"] >= r[1] - 2 and b["y1"] <= r[3] + 2
        )

    blocks = [
        b for b in _collect_blocks(page)
        if b["max_size"] >= 6.5 and not any(_inside(b, r) for r in table_regions)
    ]
    page_width = page.rect.width
    # Same column filter as _column_aware_markdown: narrow, but not stray marks.
    columns = [
        b for b in blocks
        if (b["x1"] - b["x0"]) < 0.6 * page_width and (b["x1"] - b["x0"]) >= 25
    ]
    splits = _detect_column_splits(columns, page_width)
    if not splits:
        return False

    left = [b for b in columns if b["x1"] <= splits[0]]
    right = [b for b in columns if b["x0"] >= splits[0]]
    if len(left) < 2 or len(right) < 2:
        return False

    return any(
        lb["y0"] <= rb["y1"] and rb["y0"] <= lb["y1"]
        for lb in left
        for rb in right
    )


def _reading_normalize(text: str) -> str:
    """Collapse whitespace + de-hyphenate line breaks, lowercased.

    Used to align extracted markdown against pymupdf's per-column raw text
    (the two may differ in line breaks and end-of-line hyphenation).
    """
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


def _longest_prefix_in(s: str, target: str) -> int:
    """Length of the longest word-aligned prefix of ``s`` that is in ``target``."""
    for i in range(len(s), -1, -1):
        if i < len(s) and s[i] != " ":
            continue  # not a word boundary
        if s[:i] in target:
            return i
    return 0


def _longest_suffix_in(s: str, target: str) -> int:
    """Length of the longest word-aligned suffix of ``s`` that is in ``target``."""
    for i in range(len(s), -1, -1):
        start = len(s) - i
        if start > 0 and s[start - 1] != " ":
            continue  # suffix doesn't start at a word boundary
        if s[-i:] in target:
            return i
    return 0


def _split_cross_column_paragraphs(md: str, page) -> str:
    """Re-split paragraphs that a backend glued across the two-column boundary.

    Docling's layout model occasionally merges a left-column element (e.g. the
    last cell of a side box) with the right column's opening sentence into a
    single paragraph. Using the detected column split and pymupdf's per-column
    raw text, a paragraph whose head lives in the left column and whose tail
    lives in the right column is split at that boundary.

    Degrades to ``md`` unchanged when no clear column split exists.
    """
    if not _has_pymupdf:
        return md
    split = _detect_column_split(_collect_blocks(page), page.rect.width)
    if split is None:
        return md
    width, height = page.rect.width, page.rect.height
    left_text = _reading_normalize(
        page.get_text(clip=pymupdf.Rect(0, 0, split, height))
    )
    right_text = _reading_normalize(
        page.get_text(clip=pymupdf.Rect(split, 0, width, height))
    )

    out: list[str] = []
    for para in md.split("\n\n"):
        stripped = para.strip()
        words = stripped.split()
        if len(words) < 8:
            out.append(stripped)
            continue
        norm = _reading_normalize(stripped)
        p = _longest_prefix_in(norm, left_text)
        s = _longest_suffix_in(norm, right_text)
        if not (p > 0 and s > 0 and p + s >= len(norm) - 1 and p < len(norm) - s):
            out.append(stripped)
            continue
        cut = len(norm[:p].split())
        if not (3 <= cut <= len(words) - 3):
            out.append(stripped)
            continue
        out.append(" ".join(words[:cut]))
        out.append(" ".join(words[cut:]))
    return "\n\n".join(p for p in out if p)


def _spacing_fixes(md: str) -> str:
    """Generic cosmetic spacing fixes for markdown artifacts."""
    # bold chapter cross-reference glued to the following word: **134**and
    md = re.sub(r"\*\*(\d+)\*\*(?=\S)", r"**\1** ", md)
    # underscore-italic word followed by a comma glued to the next word: _a_,_b_
    md = re.sub(r"(_[^_]+_),", r"\1, ", md)
    return md


# ═══════════════════════════════════════════════════════════════════════════════
#  Google Translate (stdlib only, no API key)
# ═══════════════════════════════════════════════════════════════════════════════

_GT_URL = "https://translate.googleapis.com/translate_a/single"
_GT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _gt_translate_one(text: str, source: str, target: str) -> str:
    """Call Google Translate API for a single chunk of text."""
    params = {
        "client": "gtx",
        "sl": source,
        "tl": target,
        "dt": "t",
        "q": text,
    }
    full_url = f"{_GT_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full_url, headers=_GT_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result and result[0]:
        return "".join(item[0] for item in result[0] if item[0])
    return text


# Markdown structural patterns protected during translation.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_TABLE_RE = re.compile(
    r"^\s*\|[^\n]*\|\s*\n\s*\|[\s:|-]+\|\s*\n(?:\s*\|[^\n]*\|\s*\n?)+",
    re.MULTILINE,
)
_SEP_CELL_RE = re.compile(r":?-{3,}:?")


def _translate_table(table: str, source: str, target: str) -> str:
    """Translate the cell contents of a markdown table, keeping its structure."""
    lines = [ln.strip() for ln in table.strip().splitlines()]
    out: list[str] = []
    cache: dict[str, str] = {}
    for ln in lines:
        if not ln.startswith("|"):
            out.append(ln)
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if all(_SEP_CELL_RE.fullmatch(c) for c in cells):
            out.append(ln)  # separator row kept verbatim
            continue
        translated: list[str] = []
        for c in cells:
            # Numbers/symbol-only cells are left untouched (faster, safer).
            if not c or not re.search(r"[A-Za-z]", c):
                translated.append(c)
                continue
            if c in cache:
                translated.append(cache[c])
                continue
            try:
                res = _gt_translate_one(c, source, target).strip()
            except Exception:
                res = c
            cache[c] = res
            translated.append(res)
        out.append("| " + " | ".join(translated) + " |")
    return "\n".join(out)


def _translate_paragraph(
    para: str, source: str, target: str, chunk_size: int
) -> str:
    """Translate one paragraph, protecting markdown tables and image links."""
    protected: dict[str, str] = {}

    def _protect(kind: str, value: str) -> str:
        tok = f"@@{kind}{len(protected)}@@"
        protected[tok] = value
        return tok

    # Image links: never send URLs to the translator.
    para = _MD_IMAGE_RE.sub(lambda m: _protect("IMG", m.group(0)), para)

    # Tables: translate their cells, then protect the rebuilt table.
    def _table_repl(m):
        return _protect("TBL", _translate_table(m.group(0), source, target))

    para = _MD_TABLE_RE.sub(_table_repl, para)

    # Nothing left to translate (only protected tokens) → skip the API call.
    if not re.search(r"[^\W\d_]", re.sub(r"@@[A-Z]+\d+@@", "", para)):
        out = para
    elif len(para) <= chunk_size:
        try:
            out = _gt_translate_one(para, source, target)
        except Exception:
            out = para
    else:
        # Long paragraph → split at sentence-ish boundaries.
        sub_paras = re.split(r"(?<=[.!?])\s+", para)
        sub_chunks: list[str] = []
        current: list[str] = []
        cur_len = 0
        for sub in sub_paras:
            if cur_len + len(sub) > chunk_size and current:
                sub_chunks.append(" ".join(current))
                current = []
                cur_len = 0
            current.append(sub)
            cur_len += len(sub)
        if current:
            sub_chunks.append(" ".join(current))
        sub_translated: list[str] = []
        for ch in sub_chunks:
            try:
                sub_translated.append(_gt_translate_one(ch, source, target))
            except Exception:
                sub_translated.append(ch)
        out = " ".join(sub_translated)

    # Google may add spaces around/inside tokens; normalize them back.
    out = re.sub(r"@@\s*([A-Z]+)\s*(\d+)\s*@@", r"@@\1\2@@", out)
    for tok, value in protected.items():
        out = out.replace(tok, value)
    return out


def translate_text_google(
    text: str, source: str = "en", target: str = "it", chunk_size: int = 1500
) -> str:
    """Translate text using Google Translate's public API.

    Translates **each paragraph independently** (split on ``\n\n``) so
    paragraph breaks never pass through the API.  Markdown tables and image
    links are protected so Google doesn't mangle their syntax; table cell
    contents are translated individually.
    """
    if not text or not text.strip():
        return text

    paragraphs = text.split("\n\n")
    translated_paras: list[str] = []
    for para in paragraphs:
        if not para.strip():
            translated_paras.append(para)
            continue
        translated_paras.append(
            _translate_paragraph(para, source, target, chunk_size)
        )
    return "\n\n".join(translated_paras)


# ═══════════════════════════════════════════════════════════════════════════════
#  widgets
# ═══════════════════════════════════════════════════════════════════════════════


class PdfPageView(QGraphicsView):
    """Left panel — displays the rendered PDF page.

    In "select mode" the user can drag a rubber-band rectangle; the selection
    is emitted in scene coordinates (full-resolution pixels of the rendered
    page), which the caller converts back to PDF points.
    """

    # x0, y0, x1, y1 in scene (full-res pixmap) coordinates
    region_selected = pyqtSignal(float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QColor(43, 43, 43))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._full_pixmap: QPixmap | None = None
        self._pix_item: QGraphicsPixmapItem | None = None
        self._text_item: QGraphicsTextItem | None = None

        self._select_mode = False
        self._rubber_item: QGraphicsRectItem | None = None
        self._rubber_origin = None
        self._band_pen = QPen(QColor(74, 144, 217), 2, Qt.PenStyle.DashLine)
        self._band_brush = QBrush(QColor(74, 144, 217, 70))

        self._show_message_text("Apri un PDF per iniziare")

    # ── scene management ──────────────────────────────────────────────

    def _clear_scene(self):
        self._scene.clear()
        self._pix_item = None
        self._text_item = None
        self._rubber_item = None
        self._rubber_origin = None

    def _show_message_text(self, text: str):
        self._clear_scene()
        item = QGraphicsTextItem(text)
        item.setDefaultTextColor(QColor(136, 136, 136))
        item.setFont(QFont("Segoe UI", 14))
        self._text_item = item
        self._scene.addItem(item)
        r = item.boundingRect()
        item.setPos(-r.width() / 2, -r.height() / 2)
        self._scene.setSceneRect(
            -r.width() / 2 - 20, -r.height() / 2 - 20,
            r.width() + 40, r.height() + 40,
        )

    def show_page(self, pixmap: QPixmap | None):
        """Store the full-resolution pixmap and scale it to fit the view."""
        if pixmap is None:
            self._full_pixmap = None
            self._show_message_text("(pagina non disponibile)")
            return
        self._full_pixmap = pixmap
        self._clear_scene()
        self._pix_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self._fit_to_view()

    def show_message(self, text: str):
        """Show a plain status message instead of a page (e.g. while loading)."""
        self._full_pixmap = None
        self._show_message_text(text)

    # ── selection mode ───────────────────────────────────────────────────

    def set_select_mode(self, enabled: bool):
        """Enable/disable rubber-band region selection."""
        self._select_mode = enabled
        self._clear_rubber()
        if enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        else:
            self.unsetCursor()

    def _clear_rubber(self):
        if self._rubber_item is not None:
            self._scene.removeItem(self._rubber_item)
            self._rubber_item = None
        self._rubber_origin = None

    # ── sizing ──────────────────────────────────────────────────────────

    def _fit_to_view(self):
        """Scale the full-resolution pixmap to fit the current view size."""
        if self._pix_item is not None:
            self.fitInView(self._pix_item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_to_view()

    # ── rubber band mouse handling ─────────────────────────────────────

    def mousePressEvent(self, event):
        if (
            self._select_mode
            and self._pix_item is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            try:
                self._clear_rubber()
                pos = self.mapToScene(event.position().toPoint())
                self._rubber_origin = pos
                self._rubber_item = QGraphicsRectItem(QRectF(pos, pos))
                self._rubber_item.setPen(self._band_pen)
                self._rubber_item.setBrush(self._band_brush)
                self._scene.addItem(self._rubber_item)
            except Exception:
                # Never let an exception escape a virtual handler: in PyQt6
                # that aborts the whole process.
                self._clear_rubber()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._select_mode
            and self._rubber_item is not None
            and self._rubber_origin is not None
        ):
            try:
                cur = self.mapToScene(event.position().toPoint())
                self._rubber_item.setRect(
                    QRectF(self._rubber_origin, cur).normalized()
                )
            except Exception:
                self._clear_rubber()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            self._select_mode
            and self._rubber_item is not None
            and self._rubber_origin is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            try:
                cur = self.mapToScene(event.position().toPoint())
                rect = QRectF(self._rubber_origin, cur).normalized()
                self._clear_rubber()
                if rect.width() >= 4.0 and rect.height() >= 4.0:
                    self.region_selected.emit(
                        rect.left(), rect.top(), rect.right(), rect.bottom()
                    )
            except Exception:
                self._clear_rubber()
            event.accept()
            return
        super().mouseReleaseEvent(event)


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


class TranslateThread(QThread):
    """Background thread for Google Translate to keep UI responsive."""

    result_ready = pyqtSignal(int, str)  # generation_id, translated_text

    def __init__(
        self,
        text: str,
        generation: int,
        source: str = "en",
        target: str = "it",
    ):
        super().__init__()
        self._text = text
        self._generation = generation
        self._source = source
        self._target = target

    def run(self):
        translated = translate_text_google(
            self._text, source=self._source, target=self._target
        )
        self.result_ready.emit(self._generation, translated)


class DoclingExtractThread(QThread):
    """Single background worker for Docling extraction (current page first,
    then prefetched pages), keeping the UI responsive.

    Docling's ``DocumentConverter`` is not thread-safe, so all conversions are
    serialized on this one thread.
    """

    # generation, page (0-based), markdown, image_uris, elapsed_secs
    page_ready = pyqtSignal(int, int, str, list, float)

    def __init__(self, extract_fn, parent=None):
        super().__init__(parent)
        self._extract_fn = extract_fn  # (page:int) -> (md:str, uris:list[str])
        self._cond = threading.Condition()
        self._priority: list[tuple[int, int]] = []  # (generation, page)
        self._prefetch: list[tuple[int, int]] = []  # (generation, page)
        self._stop = False

    def request_current(self, page: int, generation: int):
        with self._cond:
            self._priority = [(g, p) for g, p in self._priority if p != page]
            self._prefetch = [(g, p) for g, p in self._prefetch if p != page]
            self._priority.insert(0, (generation, page))
            self._cond.notify()

    def request_prefetch(self, pages, generation: int):
        with self._cond:
            cur = {p for _, p in self._priority}
            have = {p for _, p in self._prefetch}
            for p in pages:
                if p not in cur and p not in have:
                    self._prefetch.append((generation, p))
            self._cond.notify()

    def clear_prefetch(self):
        with self._cond:
            self._prefetch.clear()

    def reset(self):
        with self._cond:
            self._priority.clear()
            self._prefetch.clear()

    def stop(self):
        with self._cond:
            self._stop = True
            self._cond.notify()

    def _next_locked(self):
        if self._priority:
            return self._priority.pop(0)
        if self._prefetch:
            return self._prefetch.pop(0)
        return None

    def run(self):
        while True:
            with self._cond:
                item = self._next_locked()
                while item is None and not self._stop:
                    self._cond.wait(timeout=1.0)
                    item = self._next_locked()
                if item is None and self._stop:
                    return
            generation, page = item
            try:
                t0 = time.perf_counter()
                md, uris = self._extract_fn(page)
                elapsed = time.perf_counter() - t0
            except Exception:
                continue
            self.page_ready.emit(generation, page, md, uris, elapsed)


class TranslatablePanel(QWidget):
    """Wraps TextPanel with a tab bar to switch between original and Italian."""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Tab bar ────────────────────────────────────────────────────
        self._tab_bar = QWidget()
        self._tab_bar.setFixedHeight(36)
        self._tab_bar.setStyleSheet("""
            QWidget#tabBar {
                background: #3a3a3a;
                border-bottom: 1px solid #555;
            }
        """)
        self._tab_bar.setObjectName("tabBar")

        tab_layout = QHBoxLayout(self._tab_bar)
        tab_layout.setContentsMargins(4, 2, 4, 2)
        tab_layout.setSpacing(2)

        self._btn_original = QPushButton("📄 Originale")
        self._btn_translated = QPushButton("🇮🇹 Italiano")
        self._btn_images = QPushButton("🖼️ Immagini")

        tab_style = """
            QPushButton {
                background: #444; color: #aaa;
                border: 1px solid #555; border-bottom: none;
                border-radius: 6px 6px 0 0;
                padding: 4px 16px; font-size: 13px;
            }
            QPushButton:hover { background: #555; color: #ddd; }
            QPushButton:checked {
                background: #fff; color: #1a1a1a;
                border-color: #ddd; font-weight: bold;
            }
        """
        for btn in (self._btn_original, self._btn_translated, self._btn_images):
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setStyleSheet(tab_style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            tab_layout.addWidget(btn)

        # Spinner label for translation in progress
        self._lbl_spinner = QLabel("")
        self._lbl_spinner.setStyleSheet("color: #aaa; font-size: 13px; padding: 4px 8px;")
        tab_layout.addWidget(self._lbl_spinner)

        tab_layout.addStretch()
        self._btn_original.setChecked(True)

        # ── Text panel ─────────────────────────────────────────────────
        self.text_panel = TextPanel()

        layout.addWidget(self._tab_bar)
        layout.addWidget(self.text_panel)

        # ── Images panel (gallery of extracted figures) ────────────────
        self.images_panel = QScrollArea()
        self.images_panel.setWidgetResizable(True)
        self.images_panel.setStyleSheet(
            "QScrollArea { background: #f5f5f5; border: none; }"
        )
        self.images_panel.hide()
        layout.addWidget(self.images_panel)

        # ── State ──────────────────────────────────────────────────────
        self._original_text: str = ""
        self._translated_text: str = ""
        self._render_md: bool = True
        self._page_translation_cache: dict[int, str] = {}
        self._current_page: int = -1
        self._images: list[str] = []  # file:// URIs of current page figures
        self._thread: TranslateThread | None = None
        self._generation: int = 0  # monotonically increasing; ignore stale results

        # ── Connections ────────────────────────────────────────────────
        self._btn_original.clicked.connect(self._on_show_original)
        self._btn_translated.clicked.connect(self._on_show_translated)
        self._btn_images.clicked.connect(self._on_show_images)

        self._rebuild_images_panel()

    def show_text(
        self,
        text: str,
        as_markdown: bool = True,
        page_num: int = -1,
        force_retranslate: bool = False,
        images: list[str] | None = None,
    ):
        """Display text and prepare translation.

        If ``page_num`` is provided, the translation is cached per page.
        ``images`` are the file:// URIs of the figures extracted for the page.
        """
        self._render_md = as_markdown
        self._original_text = text
        self._current_page = page_num
        self._images = list(images or [])
        self._rebuild_images_panel()

        if self._btn_images.isChecked():
            return  # images tab active — gallery already rebuilt above

        if self._btn_translated.isChecked():
            # Translated tab is active — retranslate for the new page
            if (
                not force_retranslate
                and page_num >= 0
                and page_num in self._page_translation_cache
            ):
                self._translated_text = self._page_translation_cache[page_num]
                self.text_panel.show_text(
                    self._translated_text, as_markdown=as_markdown
                )
                self._lbl_spinner.setText("")
            elif _EXTRACTING_PLACEHOLDER in text:
                # Docling still extracting: don't translate (or cache) the
                # placeholder — wait for the real text to re-enter show_text.
                self._lbl_spinner.setText("⏳ Attendo estrazione…")
            else:
                self._start_translation(text)
        else:
            # Original tab is active (default)
            self._btn_original.setChecked(True)
            self._btn_translated.setChecked(False)
            self.text_panel.show_text(text, as_markdown=as_markdown)
            self._lbl_spinner.setText("")

    def _on_show_original(self):
        self._btn_original.setChecked(True)
        self._btn_translated.setChecked(False)
        self._btn_images.setChecked(False)
        self.images_panel.hide()
        self.text_panel.show()
        self.text_panel.show_text(self._original_text, as_markdown=self._render_md)
        self._lbl_spinner.setText("")

    def _on_show_translated(self):
        self._btn_original.setChecked(False)
        self._btn_translated.setChecked(True)
        self._btn_images.setChecked(False)
        self.images_panel.hide()
        self.text_panel.show()

        # Check cache
        if (
            self._current_page >= 0
            and self._current_page in self._page_translation_cache
        ):
            self._translated_text = self._page_translation_cache[self._current_page]
            self.text_panel.show_text(
                self._translated_text, as_markdown=self._render_md
            )
            self._lbl_spinner.setText("")
        elif _EXTRACTING_PLACEHOLDER in self._original_text:
            self._lbl_spinner.setText("⏳ Attendo estrazione…")
        else:
            self._start_translation(self._original_text)

    def _on_show_images(self):
        self._btn_original.setChecked(False)
        self._btn_translated.setChecked(False)
        self._btn_images.setChecked(True)
        self.text_panel.hide()
        self.images_panel.show()
        self._rebuild_images_panel()
        self._lbl_spinner.setText("")

    def show_images(self, images: list[str]):
        """Set the current page's figures and activate the gallery tab."""
        self._images = list(images or [])
        self._rebuild_images_panel()
        self._on_show_images()

    # ── Images gallery ─────────────────────────────────────────────────

    def _rebuild_images_panel(self):
        """Rebuild the gallery from the current page's figure URIs."""
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        if not self._images:
            hint = QLabel(
                "Nessuna immagine estratta per questa pagina.\n\n"
                "Seleziona il backend Docling 🧠 per estrarre e visualizzare le figure."
            )
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #888; font-size: 14px; padding: 24px;")
            lay.addWidget(hint)
        else:
            for uri in self._images:
                lay.addWidget(self._make_image_card(uri))
        lay.addStretch()

        old = self.images_panel.takeWidget()
        if old is not None:
            old.deleteLater()
        self.images_panel.setWidget(container)

    def _make_image_card(self, uri: str) -> QWidget:
        path = str(QUrl(uri).toLocalFile())
        card = QWidget()
        card.setObjectName("imgCard")
        card.setStyleSheet(
            "QWidget#imgCard { background: #fff; border: 1px solid #ddd;"
            " border-radius: 6px; }"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        pix = QPixmap(path)
        thumb = QLabel()
        thumb.setPixmap(
            pix.scaledToWidth(340, Qt.TransformationMode.SmoothTransformation)
        )
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        thumb.setToolTip("Clicca per ingrandire")
        thumb.mousePressEvent = lambda _e, u=uri: self._show_image_full(u)
        v.addWidget(thumb)

        info = QLabel(f"{Path(path).name}  ·  {pix.width()}×{pix.height()} px")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("border: none; color: #555; font-size: 12px;")
        v.addWidget(info)

        row = QHBoxLayout()
        btn_style = (
            "QPushButton { background: #4a90d9; color: #fff; border: none;"
            " border-radius: 4px; padding: 6px 12px; font-size: 12px; }"
            "QPushButton:hover { background: #3a7ab5; }"
        )
        btn_save = QPushButton("💾 Salva")
        btn_save.clicked.connect(lambda _=False, u=uri: self._save_image(u))
        btn_copy = QPushButton("📋 Copia")
        btn_copy.clicked.connect(lambda _=False, u=uri: self._copy_image(u))
        for b in (btn_save, btn_copy):
            b.setStyleSheet(btn_style)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
        v.addLayout(row)
        return card

    def _show_image_full(self, uri: str):
        path = str(QUrl(uri).toLocalFile())
        dlg = QDialog(self)
        dlg.setWindowTitle(Path(path).name)
        dlg.resize(900, 720)
        lay = QVBoxLayout(dlg)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        lbl = QLabel()
        lbl.setPixmap(QPixmap(path))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(lbl)
        lay.addWidget(scroll)
        dlg.exec()

    def _copy_image(self, uri: str):
        img = QImage(QUrl(uri).toLocalFile())
        if not img.isNull():
            QApplication.clipboard().setImage(img)
            self._lbl_spinner.setText("✅ Copiata")

    def _save_image(self, uri: str):
        src = str(QUrl(uri).toLocalFile())
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Salva immagine",
            Path(src).name,
            "PNG (*.png);;JPEG (*.jpg);;Tutti i file (*)",
        )
        if dest:
            shutil.copyfile(src, dest)
            self._lbl_spinner.setText("✅ Salvata")

    def _start_translation(self, text: str):
        """Fire background translation thread with generation tracking."""
        self._lbl_spinner.setText("⏳ Traducendo...")
        self._generation += 1

        # Disconnect old thread to avoid stale signals
        if self._thread is not None:
            try:
                self._thread.result_ready.disconnect()
            except TypeError:
                pass  # already disconnected

        self._thread = TranslateThread(
            text, generation=self._generation, source="en", target="it"
        )
        self._thread.result_ready.connect(self._on_translation_done)
        self._thread.start()

    def _on_translation_done(self, generation: int, translated: str):
        """Slot: background translation finished."""
        # Ignore stale results from superseded requests
        if generation != self._generation:
            return

        self._translated_text = translated

        # Cache per page
        if self._current_page >= 0:
            self._page_translation_cache[self._current_page] = translated

        # Show if translated tab still active
        if self._btn_translated.isChecked():
            self.text_panel.show_text(translated, as_markdown=self._render_md)

        self._lbl_spinner.setText("✅")

    def show_html(self, html_body: str):
        """Forward to inner TextPanel."""
        self.text_panel.setHtml(self.text_panel._HTML_CSS + html_body)

    def invalidate_cache(self):
        """Clear per-page translation cache."""
        self._page_translation_cache.clear()

    def shutdown(self):
        """Wait for any in-flight translation before the app closes."""
        if self._thread is not None:
            if self._thread.isRunning():
                self._thread.wait(5000)
            self._thread = None


class TocPanel(QWidget):
    """Dockable multi-level table of contents navigator."""

    page_selected = pyqtSignal(int)  # 0-based page index

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setStyleSheet(
            "QTreeWidget { background: #2b2b2b; color: #ddd; border: none;"
            " font-size: 13px; }"
            "QTreeWidget::item { padding: 2px 0; }"
            "QTreeWidget::item:selected { background: #3a6bc5; color: #fff; }"
        )
        layout.addWidget(self.tree)

        self._items: list[QTreeWidgetItem] = []  # flat, in document order
        self._syncing: bool = False

        self.tree.itemClicked.connect(self._on_item_clicked)

    def build_toc(self, doc) -> None:
        """Rebuild the tree from a pypdfium2 PdfDocument's bookmarks."""
        self.tree.clear()
        self._items = []

        stack: list[tuple[int, QTreeWidgetItem]] = []  # (level, item)
        try:
            bookmarks = list(doc.get_toc())
        except Exception:
            bookmarks = []

        for bm in bookmarks:
            dest = bm.get_dest()
            if dest is None:
                continue
            page_idx = dest.get_index()
            if page_idx is None:
                continue

            title = (bm.get_title() or "").strip() or "(senza titolo)"
            item = QTreeWidgetItem([f"{title}  ·  p. {page_idx + 1}"])
            item.setData(0, Qt.ItemDataRole.UserRole, page_idx)

            level = max(0, int(bm.level))
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                stack[-1][1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            stack.append((level, item))
            self._items.append(item)

        self.tree.expandAll()

    def select_page(self, page_num: int) -> None:
        """Highlight the TOC entry that best matches the given 0-based page."""
        best: QTreeWidgetItem | None = None
        for item in self._items:
            p = item.data(0, Qt.ItemDataRole.UserRole)
            if p is None:
                continue
            if p <= page_num:
                best = item
            else:
                break

        if best is None:
            return
        self._syncing = True
        self.tree.setCurrentItem(best)
        self.tree.scrollToItem(best)
        self._syncing = False

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int):
        if self._syncing:
            return
        page_idx = item.data(0, Qt.ItemDataRole.UserRole)
        if page_idx is None:
            return
        self.page_selected.emit(int(page_idx))


# ═══════════════════════════════════════════════════════════════════════════════
#  main window
# ═══════════════════════════════════════════════════════════════════════════════


class MainWindow(QMainWindow):
    # Available extraction backends
    BACKENDS = [
        "PyMuPDF4LLM ⚡", "Docling 🧠", "Smart (tabelle)",
        "pdf_oxide 🦀",
        "pdfminer.six", "pypdfium2", "pdfplumber",
    ]

    # Available rendering engines
    RENDER_ENGINES = ["pypdfium2", "PyMuPDF", "QtPdf"]

    # Available layout fixes
    FIXES = [
        "Auto",
        "Nessuno",
        "Riordino colonne",
        "Colonne + titolo in testa",
        "Spaziature",
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
        self._mupdf_doc = None       # cached pymupdf Document (render engine)
        self._qtpdf_doc = None       # cached QPdfDocument (render engine)
        self._docling_converter = None   # cached Docling converter (reused)
        self._images_dir: Path | None = None  # dir for extracted figures
        self._docling_cache: dict[int, str] = {}  # page (0-based) -> markdown
        self._docling_image_uris: dict[int, list[str]] = {}  # page -> file:// URIs
        self._mupdf_image_uris: dict[int, list[str]] = {}  # page -> auto-extracted
        self._current_images: list[str] = []  # figures for the current page
        self._docling_worker = None  # background Docling extractor (lazy)
        self._docling_generation: int = 0  # bumped on each PDF open
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

        # TOC dock (left, dockable)
        self.toc_panel = TocPanel()
        self.toc_panel.page_selected.connect(self._goto_toc_page)
        self.toc_dock = QDockWidget("Indice", self)
        self.toc_dock.setObjectName("tocDock")
        self.toc_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.toc_dock.setWidget(self.toc_panel)
        self.toc_dock.setMinimumWidth(220)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.toc_dock)
        self.toc_dock.visibilityChanged.connect(self.btn_toc.setChecked)

        # Splitter
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left — mini toolbar + scroll area wrapping the page view
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.pdf_view = PdfPageView()
        self.pdf_view.region_selected.connect(self._on_region_selected)
        self.scroll_area.setWidget(self.pdf_view)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(self._build_page_toolbar())
        left_layout.addWidget(self.scroll_area)

        # Right — text panel with translation tabs
        self.text_panel = TranslatablePanel()

        self.splitter.addWidget(left_panel)
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

        # TOC toggle
        self.btn_toc = QPushButton("📑 Indice")
        self.btn_toc.setCheckable(True)
        self.btn_toc.setChecked(True)
        self.btn_toc.setToolTip("Mostra/nascondi l'indice (TOC) del PDF")
        self.btn_toc.clicked.connect(
            lambda checked: self.toc_dock.setVisible(checked)
        )
        bar.addWidget(self.btn_toc)

        bar.addSeparator()

        # Prev
        self.btn_prev = QPushButton("◀ Prec.")
        self.btn_prev.clicked.connect(self._prev_page)
        bar.addWidget(self.btn_prev)

        # Page spin
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setValue(1)
        # Keyboard tracking off: with it on, every keystroke committed a value
        # and fired valueChanged -> _set_page -> full page render + text
        # extraction (Docling ~2-6s), freezing the box while typing. Now the
        # page changes only on Enter / focus-out (arrows still work instantly).
        self.page_spin.setKeyboardTracking(False)
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

        # Rendering engine selector
        bar.addWidget(QLabel("Render:"))
        self.render_combo = QComboBox()
        self.render_combo.addItems(self.RENDER_ENGINES)
        self.render_combo.setToolTip(
            "Motore di rendering della pagina (visualizzazione)\n"
            "pypdfium2: PDFium (predefinito)\n"
            "PyMuPDF: MuPDF, veloce\n"
            "QtPdf: nativo Qt (Chromium PDFium)"
        )
        self.render_combo.currentTextChanged.connect(self._on_render_engine_changed)
        bar.addWidget(self.render_combo)

        # Text extraction backend selector
        bar.addWidget(QLabel("Testo:"))
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(self.BACKENDS)
        self.backend_combo.setToolTip(
            "Motore di estrazione testo  |  Ctrl+B per ruotare\n"
            "PyMuPDF4LLM ⚡: Markdown nativo, tabelle, ~0.8s\n"
            "Docling 🧠: AI, massima qualità, ~2-4s (CPU, dopo il primo caricamento)\n"
            "Smart (tabelle): tabelle ASCII + pdfminer\n"
            "pdf_oxide 🦀: Rust, Markdown+tabelle, MIT license, ~0.5s\n"
            "pdfminer.six: buon flusso paragrafi (~2s)\n"
            "pypdfium2: ⚡ istantaneo, usa il doc già aperto\n"
            "pdfplumber: preciso con tabelle/colonne (~2s)"
        )
        self.backend_combo.currentTextChanged.connect(self._on_backend_changed)
        bar.addWidget(self.backend_combo)

        # Layout fix selector
        bar.addWidget(QLabel("Fix:"))
        self.fix_combo = QComboBox()
        self.fix_combo.addItems(self.FIXES)
        self.fix_combo.setToolTip(
            "Correzioni generiche al layout estratto\n"
            "Auto: applica il riordino colonne solo se la pagina lo richiede\n"
            "Nessuno: output del backend così com'è\n"
            "Riordino colonne: riordina le pagine a due colonne\n"
            "Colonne + titolo in testa: sposta il titolo del capitolo in cima\n"
            "Spaziature: correzioni cosmetiche al markdown"
        )
        self.fix_combo.currentTextChanged.connect(self._on_fix_changed)
        bar.addWidget(self.fix_combo)

    def _build_page_toolbar(self):
        """Mini toolbar shown above the PDF page viewer (left panel)."""
        bar = QToolBar("Pagina")
        bar.setMovable(False)

        # Region selection (extract the image under a mouse-drawn rectangle)
        self.btn_select_region = QPushButton("🖱️ Seleziona zona")
        self.btn_select_region.setCheckable(True)
        self.btn_select_region.setToolTip(
            "Trascina col mouse una zona della pagina\n"
            "per estrarne l'immagine nella tab 🖼️ Immagini"
        )
        self.btn_select_region.clicked.connect(self._on_select_region_toggled)
        bar.addWidget(self.btn_select_region)
        return bar

    def _display_text(
        self, text: str, page_num: int = -1, images: list[str] | None = None
    ):
        """Display extracted text respecting the current MD toggle."""
        if images is None:
            images = self._current_images
        self.text_panel.show_text(
            text, as_markdown=self._render_md, page_num=page_num, images=images
        )

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
            self._display_text(
                self._extraction_header(text, elapsed) + text,
                page_num=self._current_page,
            )

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
        elif backend == "pypdfium2":
            text = self._extract_pdfium(page_num)
        elif backend == "pdfplumber":
            text = self._extract_plumber(page_num)
        elif backend == "pdfminer.six":
            text = self._extract_pdfminer(page_num)
        else:
            text = "(backend sconosciuto)"

        # Track the figures for this page: Docling populates its own cache;
        # the other backends auto-extract embedded images via PyMuPDF.
        if backend == "Docling 🧠":
            self._current_images = self._docling_image_uris.get(page_num, [])
        else:
            self._current_images = self._get_mupdf_images(page_num)

        # Docling cache miss → extraction runs in background; show a hint.
        if text is None:
            return _EXTRACTING_PLACEHOLDER, time.perf_counter() - t0

        # Apply the selected layout fix
        text = self._apply_fix(text, page_num)

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

    # ── image extraction (PyMuPDF) ──────────────────────────────────────

    def _get_mupdf_images(self, page_num: int) -> list[str]:
        """Auto-extract (cached) the embedded images of a page via PyMuPDF."""
        if page_num in self._mupdf_image_uris:
            return self._mupdf_image_uris[page_num]
        uris = self._extract_embedded_images(page_num)
        self._mupdf_image_uris[page_num] = uris
        return uris

    def _extract_embedded_images(self, page_num: int) -> list[str]:
        """Extract the page's embedded raster images into the figures dir."""
        doc = self._get_mupdf_doc()
        if doc is None:
            return []
        images_dir = self._get_images_dir()
        prefix = f"page_{page_num + 1:04d}_auto_"
        for old in images_dir.glob(prefix + "*"):
            old.unlink(missing_ok=True)

        uris: list[str] = []
        for data, ext in _page_embedded_images(doc, page_num):
            path = images_dir / f"{prefix}{len(uris)}.{ext}"
            try:
                path.write_bytes(data)
            except Exception:
                continue
            uris.append(path.resolve().as_uri())
        return uris

    def _extract_image_region(self, page_num: int, clip) -> str | None:
        """Extract the image in a PDF-points rect and save it (file:// URI)."""
        doc = self._get_mupdf_doc()
        if doc is None or not _has_pymupdf:
            return None
        result = _region_image(
            doc, page_num, clip, max(self._render_scale, 4.0)
        )
        if result is None:
            return None
        data, ext = result
        images_dir = self._get_images_dir()
        prefix = f"page_{page_num + 1:04d}_region_"
        for old in images_dir.glob(prefix + "*"):
            old.unlink(missing_ok=True)
        path = images_dir / f"{prefix}0.{ext}"
        try:
            path.write_bytes(data)
        except Exception:
            return None
        return path.resolve().as_uri()

    def _on_region_selected(self, x0: float, y0: float, x1: float, y1: float):
        """Extract the user-selected page region and show it in the gallery."""
        self._set_select_mode(False)
        scale = self._render_scale or 1.0
        clip = (x0 / scale, y0 / scale, x1 / scale, y1 / scale)
        uri = self._extract_image_region(self._current_page, clip)
        if uri is None:
            self.status_bar.showMessage(
                "Nessuna immagine estraibile dalla zona selezionata"
            )
            return
        if uri not in self._current_images:
            self._current_images.append(uri)
        self.text_panel.show_images(self._current_images)
        name = Path(QUrl(uri).toLocalFile()).name
        self.status_bar.showMessage(f"Immagine estratta dalla zona: {name}")

    def _on_select_region_toggled(self, checked: bool):
        """Enable/disable rubber-band selection on the left panel."""
        self.pdf_view.set_select_mode(checked)

    def _set_select_mode(self, enabled: bool):
        """Update both the toggle button and the page view selection mode."""
        self.btn_select_region.setChecked(enabled)
        self.pdf_view.set_select_mode(enabled)

    def _get_docling_converter(self):
        """Build (once, lazily) the Docling converter with picture images enabled."""
        if self._docling_converter is None and _has_docling:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.images_scale = 2.0
            pipeline_options.generate_picture_images = True
            self._docling_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=pipeline_options
                    )
                }
            )
        return self._docling_converter

    def _get_images_dir(self) -> Path:
        """Return (creating on first use) the per-document figures directory."""
        if self._images_dir is None:
            base = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
            if not base:
                base = str(Path.home() / ".noesis-pdf-reader")
            self._images_dir = Path(base) / "images" / self._pdf_path.stem
        self._images_dir.mkdir(parents=True, exist_ok=True)
        return self._images_dir

    def _save_docling_images(self, result, page_num: int) -> list[str]:
        """Extract Docling PictureItems for a page into a temp dir (file:// URIs)."""
        images_dir = self._get_images_dir()
        prefix = f"page_{page_num + 1:04d}_img_"
        for old in images_dir.glob(prefix + "*.png"):
            old.unlink(missing_ok=True)

        uris: list[str] = []
        i = 0
        for element, _level in result.document.iterate_items():
            if not isinstance(element, PictureItem):
                continue
            try:
                img = element.get_image(result.document)
            except Exception:
                continue
            if img is None:
                continue
            path = images_dir / f"{prefix}{i}.png"
            img.convert("RGB").save(path)
            uris.append(path.resolve().as_uri())
            i += 1
        return uris

    def _docling_extract_page(self, page_num: int) -> tuple[str, list[str]]:
        """Run Docling on one page (worker-thread side). Returns (md, image_uris)."""
        converter = self._get_docling_converter()
        result = converter.convert(
            str(self._pdf_path), page_range=(page_num + 1, page_num + 1)
        )
        placeholder = "!!PIC!!"
        md = result.document.export_to_markdown(image_placeholder=placeholder)
        uris = self._save_docling_images(result, page_num)
        for uri in uris:
            md = md.replace(placeholder, f"![figura]({uri})", 1)
        md = clean_text(md).strip() or "(nessun testo estraibile su questa pagina)"
        # Fix: Docling occasionally glues a left-column element to the right
        # column's opening sentence; re-split across the column boundary.
        if _has_pymupdf:
            try:
                mupdf_doc = pymupdf.open(str(self._pdf_path))
                try:
                    md = _split_cross_column_paragraphs(md, mupdf_doc[page_num])
                finally:
                    mupdf_doc.close()
            except Exception:
                pass
        return md, uris

    def _extract_docling(self, page_num: int) -> str | None:
        """Docling: return cached markdown, or None and schedule background work."""
        if not _has_docling:
            return "(docling non installato — esegui: pip install docling)"
        if page_num in self._docling_cache:
            return self._docling_cache[page_num]
        self._ensure_docling_worker()
        self._docling_worker.request_current(page_num, self._docling_generation)
        return None

    def _ensure_docling_worker(self):
        if self._docling_worker is None and _has_docling:
            self._docling_worker = DoclingExtractThread(
                self._docling_extract_page, self
            )
            self._docling_worker.page_ready.connect(self._on_docling_page_ready)
            self._docling_worker.start()
        return self._docling_worker

    def _on_docling_page_ready(self, generation, page_num, md, uris, elapsed):
        """Slot: a background Docling extraction finished."""
        if generation != self._docling_generation:
            return  # stale result from a previous document
        self._docling_cache[page_num] = md
        self._docling_image_uris[page_num] = uris
        if page_num != self._current_page:
            return  # prefetched page — cached for later
        self._current_images = uris
        text = self._apply_fix(md, page_num)
        self._display_text(
            self._extraction_header(text, elapsed) + text,
            page_num=page_num,
            images=uris,
        )
        self.status_bar.showMessage(
            f"Pagina {page_num + 1} di {self._page_count}"
            f"  —  {self._pdf_path.name}"
            f"  |  Testo: {self.backend_combo.currentText()} ({elapsed*1000:.0f} ms)"
        )

    def _schedule_prefetch(self, page_num: int):
        """Queue the next 5 pages for background extraction (Docling only)."""
        if self.backend_combo.currentText() != "Docling 🧠" or not _has_docling:
            return
        worker = self._ensure_docling_worker()
        worker.clear_prefetch()
        pending = [
            p for p in range(page_num + 1, min(page_num + 6, self._page_count))
            if p not in self._docling_cache
        ]
        if pending:
            worker.request_prefetch(pending, self._docling_generation)

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
        self._display_page(page_num)

        # Extract text right
        if self._pdf_path:
            text, elapsed = self._extract_text(page_num)
            backend = self.backend_combo.currentText()
            self._display_text(
                self._extraction_header(text, elapsed) + text, page_num=page_num
            )

        # Update toolbar
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page_num + 1)
        self.page_spin.blockSignals(False)

        self.status_bar.showMessage(
            f"Pagina {page_num + 1} di {count}  —  {self._pdf_path.name}"
            f"  |  Testo: {backend} ({elapsed*1000:.0f} ms)"
        )

        # Sync TOC highlight
        self.toc_panel.select_page(page_num)

        # Prefetch the next pages in background (Docling only).
        self._schedule_prefetch(page_num)

    def _next_page(self):
        self._set_page(self._current_page + 1)

    def _prev_page(self):
        self._set_page(self._current_page - 1)

    def _on_spin(self, val: int):
        self._set_page(val - 1)

    def _goto_toc_page(self, page_idx: int):
        """Navigate to a page selected from the TOC."""
        self._set_page(page_idx)

    def _on_backend_changed(self, _text: str):
        """Re-extract text for current page when backend changes."""
        self._plumber_cache.clear()
        self._pdfoxide_doc = None
        self.text_panel.invalidate_cache()
        self._reextract_current()

    def _cycle_backend(self):
        """Cycle through backends via Ctrl+B."""
        idx = self.backend_combo.currentIndex()
        nxt = (idx + 1) % len(self.BACKENDS)
        self.backend_combo.setCurrentIndex(nxt)

    def _on_fix_changed(self, _text: str):
        """Re-extract current page when the layout fix changes."""
        self.text_panel.invalidate_cache()
        self._reextract_current()

    def _reextract_current(self):
        """Re-extract + display text for the current page."""
        if self._pdf_doc is None or self._page_count == 0 or not self._pdf_path:
            return
        text, elapsed = self._extract_text(self._current_page)
        self._display_text(
            self._extraction_header(text, elapsed) + text,
            page_num=self._current_page,
        )
        self.status_bar.showMessage(
            f"Pagina {self._current_page + 1} di {self._page_count}"
            f"  —  {self._pdf_path.name}"
            f"  |  Testo: {self.backend_combo.currentText()} ({elapsed*1000:.0f} ms)"
        )

    def _extraction_header(self, text: str, elapsed: float) -> str:
        """Build the header line shown above the extracted text."""
        fix = self.fix_combo.currentText()
        fix_part = f"  │  Fix: {fix}" if fix != "Nessuno" else ""
        return (
            f"── Backend: {self.backend_combo.currentText()}"
            f"  │  {elapsed*1000:.1f} ms"
            f"  │  {len(text)} caratteri{fix_part} ──\n\n"
        )

    def _apply_fix(self, text: str, page_num: int) -> str:
        """Apply the selected layout fix to extracted text."""
        fix = self.fix_combo.currentText()
        if fix == "Nessuno":
            return text
        if fix == "Spaziature":
            return _spacing_fixes(text)
        doc = self._get_mupdf_doc()
        if doc is None:
            return text
        if fix == "Auto":
            # Adaptive: reorder only if the page really needs it and the
            # backend doesn't already produce correct reading order.
            if self.backend_combo.currentText() == "Docling 🧠":
                return text
            try:
                if not _page_needs_column_reorder(doc[page_num]):
                    return text
                return _column_aware_markdown(doc[page_num]) or text
            except Exception:
                return text
        move_title = fix == "Colonne + titolo in testa"
        try:
            return _column_aware_markdown(doc[page_num], move_title=move_title) or text
        except Exception:
            return text

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
            self._display_page(self._current_page)

    # ── rendering engine ──────────────────────────────────────────────────

    def _get_mupdf_doc(self):
        """Open (lazily) the pymupdf document for rendering."""
        if self._mupdf_doc is None and self._pdf_path and _has_pymupdf:
            try:
                self._mupdf_doc = pymupdf.open(str(self._pdf_path))
            except Exception:
                self._mupdf_doc = None
        return self._mupdf_doc

    def _get_qtpdf_doc(self):
        """Open (lazily) the QPdfDocument for rendering."""
        if self._qtpdf_doc is None and self._pdf_path and _has_qtpdf:
            doc = QPdfDocument(self)
            doc.statusChanged.connect(self._on_qtpdf_status)
            self._qtpdf_doc = doc
            doc.load(str(self._pdf_path))
        return self._qtpdf_doc

    def _render_pymupdf(self, page_num: int) -> QPixmap | None:
        doc = self._get_mupdf_doc()
        if doc is None:
            return None
        try:
            page = doc[page_num]
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(self._render_scale, self._render_scale)
            )
            img = QImage(
                pix.samples, pix.width, pix.height, pix.stride,
                QImage.Format.Format_RGB888,
            )
            return QPixmap.fromImage(img)
        except Exception:
            return None

    def _render_qtpdf(self, page_num: int) -> QPixmap | None:
        doc = self._get_qtpdf_doc()
        if doc is None or doc.status() != QPdfDocument.Status.Ready:
            return None
        try:
            pt = doc.pagePointSize(page_num)
            w = max(1, int(pt.width() * self._render_scale))
            h = max(1, int(pt.height() * self._render_scale))
            img = doc.render(page_num, QSize(w, h))
            if img.isNull():
                return None
            return QPixmap.fromImage(img)
        except Exception:
            return None

    def _render_page(self, page_num: int) -> QPixmap | None:
        """Render a page with the currently selected engine."""
        engine = self.render_combo.currentText()
        if engine == "PyMuPDF":
            return self._render_pymupdf(page_num)
        if engine == "QtPdf":
            return self._render_qtpdf(page_num)
        # pypdfium2 (default — canonical doc is already open)
        return pdfium_page_to_pixmap(
            self._pdf_doc[page_num], scale=self._render_scale
        )

    def _display_page(self, page_num: int):
        """Render + show a page, with informative fallback messages."""
        engine = self.render_combo.currentText()
        pix = self._render_page(page_num)
        if pix is not None:
            self.pdf_view.show_page(pix)
            return
        if engine == "PyMuPDF" and not _has_pymupdf:
            self.pdf_view.show_message("(pymupdf non installato)")
        elif engine == "QtPdf":
            if not _has_qtpdf:
                self.pdf_view.show_message("(QtPdf non disponibile)")
            elif (
                self._qtpdf_doc is not None
                and self._qtpdf_doc.status() == QPdfDocument.Status.Loading
            ):
                self.pdf_view.show_message("(caricamento PDF in corso…)")
            else:
                self.pdf_view.show_message("(pagina non disponibile con QtPdf)")
        else:
            self.pdf_view.show_message("(pagina non disponibile)")

    def _on_qtpdf_status(self, status):
        """Re-render when QtPdf finishes loading (or report an error)."""
        if self.sender() is not self._qtpdf_doc:
            return  # stale document
        if self.render_combo.currentText() != "QtPdf":
            return
        if self._pdf_doc is None or self._page_count == 0:
            return
        if status == QPdfDocument.Status.Ready:
            self._display_page(self._current_page)
        elif status == QPdfDocument.Status.Error:
            self.pdf_view.show_message("(errore caricamento PDF con QtPdf)")

    def _on_render_engine_changed(self, _text: str):
        """Re-render the current page when the engine changes."""
        if self._pdf_doc is not None and self._page_count > 0:
            self._display_page(self._current_page)

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

        # Reset auxiliary render documents
        if self._mupdf_doc is not None:
            self._mupdf_doc.close()
            self._mupdf_doc = None
        if self._qtpdf_doc is not None:
            self._qtpdf_doc.close()
            self._qtpdf_doc.deleteLater()
            self._qtpdf_doc = None

        try:
            self._pdf_doc = pdfium.PdfDocument(str(path))
            self._pdf_path = path
            self._page_count = len(self._pdf_doc)

            self._plumber_cache.clear()
            self._pdfoxide_doc = None
            self._images_dir = None
            self._docling_cache.clear()
            self._docling_image_uris.clear()
            self._mupdf_image_uris.clear()
            self._current_images = []
            self._docling_generation += 1
            if self._docling_worker is not None:
                self._docling_worker.reset()
            self.text_panel.invalidate_cache()

            self.page_spin.setEnabled(True)
            self.page_spin.setMaximum(max(self._page_count, 1))
            self.lbl_total.setText(str(self._page_count))

            # Build the multi-level table of contents
            self.toc_panel.build_toc(self._pdf_doc)

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
        if self._mupdf_doc is not None:
            self._mupdf_doc.close()
            self._mupdf_doc = None
        if self._qtpdf_doc is not None:
            self._qtpdf_doc.close()
            self._qtpdf_doc = None
        self._plumber_cache.clear()
        self._pdfoxide_doc = None
        if self._docling_worker is not None:
            self._docling_worker.stop()
            # Only drop the reference if the thread actually finished, to avoid
            # destroying a still-running QThread.
            if self._docling_worker.wait(5000):
                self._docling_worker = None
        self.text_panel.shutdown()
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════════════════════
#  entry point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("noesis-pdf-reader")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
