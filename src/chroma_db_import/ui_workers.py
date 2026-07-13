from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QObject, Signal
from chroma_db_import.ui_export import export_chroma
from chroma_db_import.ui_models import ImportPlan, ImportProgress
from chroma_db_import.ui_support import TORCH_CUDA_INDEX_URL

class ChromaExportWorker(QObject):
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, plan: ImportPlan, mode: str) -> None:
        super().__init__()
        self.plan = plan
        self.mode = mode

    def run(self) -> None:
        try:
            self.progress.emit(ImportProgress(f"Preparing {self.mode} export...", 0, 0))
            summary = export_chroma(self.plan, self.mode, self.progress.emit)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(summary)

class CudaTorchInstallWorker(QObject):
    progress = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def run(self) -> None:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            "torch",
            "torchvision",
            "torchaudio",
            "--index-url",
            TORCH_CUDA_INDEX_URL,
        ]
        self.progress.emit("Installing CUDA-enabled PyTorch. This can download several GB and may take a while.")
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return

        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip()
            if text:
                self.progress.emit(text)
        return_code = process.wait()
        if return_code:
            self.failed.emit(f"pip exited with code {return_code}")
            return
        self.finished.emit("CUDA-enabled PyTorch install completed. Restarting device detection.")
