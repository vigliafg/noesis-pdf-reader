# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Noesis PDF Reader.

Builds a one-folder bundle ("onedir"). Two variants, selected via the
``NOESIS_DOCLING`` environment variable:

    NOESIS_DOCLING=0 pyinstaller --clean --noconfirm packaging/noesis.spec   # light
    NOESIS_DOCLING=1 pyinstaller --clean --noconfirm packaging/noesis.spec   # medium

``light`` ships the fast backends only (no Docling/torch). ``medium`` adds
Docling (torch CPU-only) as the quality backend. Docling's model weights are
NOT bundled: they are downloaded to the user cache on first use, exactly as
when running from source. The "full" tier (CUDA) is source-only: see
``requirements-cuda.txt``.
"""

import os

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
)

# Spec files are executed with SPECPATH pointing at this file's directory.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821

DOCLING = os.environ.get("NOESIS_DOCLING", "0") == "1"

datas = []
binaries = []
hiddenimports = []

# pymupdf4llm's layout analyzer loads ONNX models shipped inside pymupdf
# (pymupdf/layout/resources/*). PyInstaller's pymupdf hook misses them.
datas += collect_data_files("pymupdf")

# PyQt6.QtPdf is imported lazily (try/except) in main.py; make sure it is kept.
hiddenimports += collect_submodules("PyQt6.QtPdf")

if DOCLING:
    # Docling has no PyInstaller hook yet, so collect it (and its native/Rust
    # subpackages) explicitly. torch/transformers are covered by the hooks that
    # ship with pyinstaller-hooks-contrib.
    for pkg in (
        "docling",
        "docling_core",
        "docling_parse",
        "docling_ibm_models",
        "tokenizers",
        "safetensors",
    ):
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "IPython",
        "jupyter",
        "pytest",
        "setuptools",
        "pip",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NoesisPDFReader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NoesisPDFReader",
)
