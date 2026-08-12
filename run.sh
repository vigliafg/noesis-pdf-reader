#!/bin/bash
# Noesis PDF Reader — script di lancio con percorsi assoluti
# Non richiede l'attivazione manuale dell'ambiente virtuale

cd /home/vigliafg/Documenti/GitHub/noesis-pdf-reader || exit 1
exec /home/vigliafg/Documenti/GitHub/noesis-pdf-reader/.venv/bin/python /home/vigliafg/Documenti/GitHub/noesis-pdf-reader/main.py "$@"
