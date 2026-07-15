from __future__ import annotations

import subprocess
import sys
import datetime as dt
import json

from PySide6.QtCore import QObject, Signal
from chroma_db_import.ui_export import export_chroma, preview_ui_import_plan
from chroma_db_import.ui_models import ImportPlan, ImportProgress
from chroma_db_import.ui_support import TORCH_CUDA_INDEX_URL
from chroma_db_import.staging import operation_id

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
            preview = preview_ui_import_plan(self.plan, self.mode)
            self.progress.emit(ImportProgress(
                f"Plan: {preview['source_episodes']} source episode(s), {preview['eligible_records']} eligible record(s); "
                f"invalid={preview['invalid_records']}; embedding={preview['embedding']['compatibility']}; staging={preview['staging_destination']}", 0, 0
            ))
            self.progress.emit(ImportProgress(f"Preparing {self.mode} export...", 0, 0))
            summary = export_chroma(self.plan, self.mode, self.progress.emit)
        except Exception as exc:
            report_dir = self.plan.output_root / "state" / "import_reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"{operation_id('ui-failed')}.json"
            report_path.write_text(json.dumps({
                "status": "failed",
                "failed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "mode": self.mode,
                "export_dir": str(self.plan.export_dir),
                "error": f"{type(exc).__name__}: {exc}",
            }, indent=2), encoding="utf-8")
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
