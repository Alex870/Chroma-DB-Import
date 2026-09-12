from __future__ import annotations

import subprocess
import sys
import datetime as dt
import json
import os
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from chroma_db_import.ui_export import export_chroma, preview_ui_import_plan
from chroma_db_import.ui_models import ImportPlan, ImportProgress
from chroma_db_import.ui_support import TORCH_CUDA_INDEX_URL
from chroma_db_import.staging import operation_id
from chroma_db_import.managed import ManagedCatalog, discover, run_managed_import
from chroma_db_import.config import ImportConfig
from chroma_db_import.dedup_artifacts import validate_dedup_artifacts
from chroma_db_import.podcast_rag_adapter import PodcastRagSourceAdapter


class RedundancyWorker(QObject):
    """Run one catalog-backed redundancy command away from the Qt UI thread."""

    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, arguments: list[str]):
        super().__init__()
        self.arguments = list(arguments)
        self.process: subprocess.Popen[str] | None = None
        self.cancel_requested = False
        self.cancel_file: Path | None = None

    def cancel(self) -> None:
        self.cancel_requested = True
        if self.cancel_file is not None:
            try:
                self.cancel_file.parent.mkdir(parents=True, exist_ok=True)
                self.cancel_file.touch(exist_ok=True)
                return
            except OSError:
                pass
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()

    def run(self) -> None:
        if self.cancel_requested:
            self.failed.emit("Redundancy operation cancelled before it started.")
            return
        self.progress.emit("Running semantic redundancy operation...")
        try:
            source_root = str(Path(__file__).resolve().parents[1])
            environment = os.environ.copy()
            current_pythonpath = environment.get("PYTHONPATH")
            environment["PYTHONPATH"] = source_root if not current_pythonpath else source_root + os.pathsep + current_pythonpath
            command_arguments = list(self.arguments)
            if command_arguments and command_arguments[0] == "assess":
                catalog_index = command_arguments.index("--catalog") if "--catalog" in command_arguments else -1
                catalog_path = Path(command_arguments[catalog_index + 1]).expanduser().resolve() if catalog_index >= 0 and catalog_index + 1 < len(command_arguments) else Path.cwd() / "state" / "context_catalog.sqlite3"
                self.cancel_file = catalog_path.parent / "redundancy_cancel" / f"{uuid.uuid4().hex}.cancel"
                command_arguments.extend(["--cancel-file", str(self.cancel_file)])
            self.process = subprocess.Popen(
                [sys.executable, "-m", "chroma_db_import.cli", "redundancy", *command_arguments],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        assert self.process.stdout is not None
        output_lines: list[str] = []
        for line in self.process.stdout:
            text = line.rstrip()
            if text:
                output_lines.append(text)
                self.progress.emit(text)
        exit_code = int(self.process.wait())
        self.process = None
        if self.cancel_file is not None:
            try:
                self.cancel_file.unlink(missing_ok=True)
            except OSError:
                pass
        output = "\n".join(output_lines)
        if self.cancel_requested:
            self.failed.emit("Redundancy operation cancelled. No active export pointer was changed.")
            return
        if exit_code not in (0, 2):
            self.failed.emit(output or f"Redundancy operation failed with exit code {exit_code}")
            return
        self.finished.emit({"exit_code": exit_code, "output": output, "error": ""})

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


class ManagedImportWorker(QObject):
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        config: ImportConfig,
        project_dir: Path,
        catalog_path: Path,
        partition_id: str,
        output_root: Path | None,
        upstream_release_id: str | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.project_dir = project_dir
        self.catalog_path = catalog_path
        self.partition_id = partition_id
        self.output_root = output_root
        self.upstream_release_id = upstream_release_id

    def run(self) -> None:
        self.progress.emit(ImportProgress(f"Validating release for {self.partition_id}...", 0, 0))
        try:
            with ManagedCatalog(self.catalog_path) as catalog:
                def report(message: str, current: int, total: int) -> None:
                    self.progress.emit(ImportProgress(message, current, total))

                result = run_managed_import(
                    self.config,
                    self.project_dir,
                    catalog,
                    self.partition_id,
                    upstream_release_id=self.upstream_release_id,
                    output_root=self.output_root,
                    progress_callback=report,
                )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(result)


class ManagedContextWorkflowWorker(QObject):
    """Inspect published source artifacts and prepare a Chroma import."""

    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        config: ImportConfig,
        project_dir: Path,
        catalog_path: Path,
        partition_id: str,
        output_root: Path | None,
        operation: str,
    ) -> None:
        super().__init__()
        self.config = config
        self.project_dir = project_dir
        self.catalog_path = catalog_path
        self.partition_id = partition_id
        self.output_root = output_root
        self.operation = operation

    def run(self) -> None:
        try:
            with ManagedCatalog(self.catalog_path) as catalog:
                context = catalog.context(self.partition_id)
                if not context:
                    raise ValueError(f"unknown managed context: {self.partition_id}")
                source_root_value = str(context.get("source_root") or "")
                if not source_root_value:
                    raise ValueError("selected context has no linked producer source root")
                adapter = PodcastRagSourceAdapter(Path(source_root_value), self.partition_id)

                self.progress.emit(ImportProgress("Checking producer processing status...", 0, 0))
                status = adapter.inspect()
                catalog.set_setting(
                    f"producer_status:{self.partition_id}",
                    json.dumps(status.as_dict(), ensure_ascii=False, sort_keys=True),
                )
                if self.operation == "inspect":
                    self.finished.emit({"status": "inspected", "source_status": status.as_dict()})
                    return

                if self.operation == "resume":
                    self.finished.emit({
                        "status": "producer_action_external",
                        "source_status": status.as_dict(),
                    })
                    return

                if not status.ready_to_publish:
                    self.finished.emit({"status": "blocked_pending", "source_status": status.as_dict()})
                    return

                release = adapter.publish_release(
                    lambda message: self.progress.emit(ImportProgress(message, 0, 0))
                )
                self.progress.emit(ImportProgress("Discovering the published release...", 0, 0))
                discover(catalog, [Path(source_root_value)])
                self.progress.emit(ImportProgress("Validating the prospective Chroma import...", 0, 0))
                def report(message: str, current: int, total: int) -> None:
                    self.progress.emit(ImportProgress(message, current, total))

                preview = run_managed_import(
                    self.config,
                    self.project_dir,
                    catalog,
                    self.partition_id,
                    upstream_release_id=str(release["release_id"]),
                    output_root=self.output_root,
                    dry_run=True,
                    progress_callback=report,
                )
                self.finished.emit(
                    {
                        "status": "ready_for_import",
                        "source_status": status.as_dict(),
                        "release": release,
                        "preview": preview,
                    }
                )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ManagedDedupPreviewWorker(QObject):
    """Run contract validation and dedup planning without provider or writes."""
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, config: ImportConfig, project_dir: Path, catalog_path: Path, partition_id: str, output_root: Path | None = None):
        super().__init__()
        self.config = config
        self.project_dir = project_dir
        self.catalog_path = catalog_path
        self.partition_id = partition_id
        self.output_root = output_root

    def run(self) -> None:
        try:
            with ManagedCatalog(self.catalog_path) as catalog:
                def report(message: str, current: int, total: int) -> None:
                    self.progress.emit(ImportProgress(message, current, total))

                result = run_managed_import(
                    self.config,
                    self.project_dir,
                    catalog,
                    self.partition_id,
                    output_root=self.output_root,
                    dry_run=True,
                    progress_callback=report,
                )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(result)


class ManagedDedupReviewWorker(QObject):
    """Read and validate an already-published dedup ledger."""
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, export_root: Path, release: dict):
        super().__init__()
        self.export_root = export_root
        self.release = release

    def run(self) -> None:
        try:
            self.finished.emit(validate_dedup_artifacts(self.export_root, self.release))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
