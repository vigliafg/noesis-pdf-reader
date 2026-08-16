# Build a Noesis PDF Reader release bundle with PyInstaller (Windows).
#
# Usage (PowerShell):
#   .\packaging\build.ps1 -Variant light
#   .\packaging\build.ps1 -Variant medium
#
# Creates a fresh venv (.\.venv-build), installs the right dependencies
# (CPU-only torch for the "medium" variant) and produces dist\NoesisPDFReader.
# The "full" tier (CUDA) is source-only: see requirements-cuda.txt.

param(
  [ValidateSet("light", "medium")]
  [string]$Variant = "light"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$Venv = Join-Path $Root ".venv-build"
$VenvPy = Join-Path $Venv "Scripts\python.exe"

Write-Host "==> creating venv ($Venv)"
& $Python -m venv $Venv
& $VenvPy -m pip install --upgrade pip

Write-Host "==> installing light dependencies"
& $VenvPy -m pip install -r requirements.txt

if ($Variant -eq "medium") {
  Write-Host "==> installing CPU-only torch + docling"
  & $VenvPy -m pip install torch --index-url https://download.pytorch.org/whl/cpu
  & $VenvPy -m pip install -r requirements-docling.txt
}

Write-Host "==> installing PyInstaller"
& $VenvPy -m pip install "pyinstaller>=6.16"

Write-Host "==> building ($Variant)"
if ($Variant -eq "medium") {
  $env:NOESIS_DOCLING = "1"
} else {
  $env:NOESIS_DOCLING = "0"
}
& $VenvPy -m PyInstaller --clean --noconfirm packaging\noesis.spec

Write-Host "==> done: dist\NoesisPDFReader"
