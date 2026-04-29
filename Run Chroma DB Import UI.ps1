$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install -r chroma_db_import_requirements.txt
.\.venv\Scripts\pythonw.exe .\chroma_db_import_ui.py
