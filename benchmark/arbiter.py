"""AI OCR arbiter: GLM-OCR via Ollama's native ``/api/generate`` endpoint.

Serves as the *gold* reference for reliability benchmarks. Renders a PDF page
to a PNG at a given DPI and asks GLM-OCR to transcribe it in reading order.

Ollama must be running locally with the model pulled::

    ollama pull glm-ocr:latest
"""

from __future__ import annotations

import base64
import json
import urllib.request
from pathlib import Path

import pymupdf

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "glm-ocr:latest"
# Task prompt understood by the glm-ocr renderer (see ollama.com/library/glm-ocr).
PROMPT = "Text Recognition: ./image.png"


def render_page_png(pdf_path: str | Path, page_1based: int, dpi: int = 150) -> bytes:
    """Render one PDF page to PNG bytes (RGB)."""
    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc[page_1based - 1]
        scale = dpi / 72.0
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        return pix.tobytes("png")
    finally:
        doc.close()


def glm_ocr_transcribe(
    png_bytes: bytes,
    model: str = MODEL,
    prompt: str = PROMPT,
    base_url: str = OLLAMA_URL,
    timeout: int = 600,
) -> str:
    """Send a PNG to GLM-OCR and return the transcribed text."""
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [base64.b64encode(png_bytes).decode()],
        "stream": False,
    }
    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return (data.get("response") or "").strip()


def extract_glm_ocr(pdf_path: str | Path, page_1based: int, dpi: int = 150) -> str:
    """Render + transcribe one page with GLM-OCR (the gold reference)."""
    png = render_page_png(pdf_path, page_1based, dpi=dpi)
    return glm_ocr_transcribe(png)
