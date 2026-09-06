from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDir, Qt, QThread, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chroma_db_import.config import ImportConfig
from chroma_db_import.contract import validate_podcast_metadata
from chroma_db_import.managed import ManagedCatalog, discover, import_profile_payload
from chroma_db_import.deduplication import resolve_dedup_policy
from chroma_db_import.dedup_artifacts import validate_dedup_artifacts
from chroma_db_import.ui_export import build_ui_validation_report, preview_ui_import_plan, preview_ui_reconciliation, select_documents_for_episode, update_should_skip_episode
from chroma_db_import.ui_export import should_include_document
from chroma_db_import.ui_helpers import safe_folder_name, slugify
from chroma_db_import.ui_loader import EpisodeLoader
from chroma_db_import.ui_models import Episode, ImportPlan, ImportProgress, ImportSummary, ProcessedDocument
from chroma_db_import.ui_support import (
    TORCH_CUDA_INDEX_URL,
    embedding_device_options,
    is_descendant_of,
    normalize_embedding_device_value,
    pytorch_cuda_is_available,
    resolve_embedding_device,
)
from chroma_db_import.ui_workers import ChromaExportWorker, CudaTorchInstallWorker, ManagedDedupPreviewWorker, ManagedDedupReviewWorker, ManagedImportWorker, RedundancyWorker

class ProcessedFolderPreviewDialog(QDialog):
    """Browse for a processed-data folder and preview compatible files before accepting."""

    def __init__(self, parent: QWidget | None = None, start_path: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open processed RAG output folder")
        self.resize(1040, 620)
        self.selected_path: Path | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self.refresh_preview)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Choose a folder that contains Podcast-RAG-pipeline *.processed_documents.json files. "
            "The preview updates before you commit."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        body = QHBoxLayout()
        layout.addLayout(body, 1)

        left = QVBoxLayout()
        body.addLayout(left, 2)

        left.addWidget(QLabel("Suggested Locations"))
        self.location_list = QListWidget()
        self.location_list.itemSelectionChanged.connect(self.handle_location_selected)
        left.addWidget(self.location_list)

        left.addWidget(QLabel("Folder Browser"))
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        left.addWidget(self.path_edit)

        self.fs_model = QFileSystemModel(self)
        self.fs_model.setFilter(
            self.fs_model.filter() | QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot
        )
        self.fs_model.setRootPath("")

        self.tree_view = QTreeView()
        self.tree_view.setModel(self.fs_model)
        self.tree_view.setHeaderHidden(True)
        for column in range(1, self.fs_model.columnCount()):
            self.tree_view.hideColumn(column)
        self.tree_view.selectionModel().selectionChanged.connect(lambda *_args: self.handle_tree_selection_changed())
        left.addWidget(self.tree_view, 1)

        right = QVBoxLayout()
        body.addLayout(right, 3)

        self.status_label = QLabel("No folder selected.")
        self.status_label.setWordWrap(True)
        right.addWidget(self.status_label)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        right.addWidget(self.detail_label)

        self.metrics_label = QLabel("")
        self.metrics_label.setWordWrap(True)
        right.addWidget(self.metrics_label)

        self.sample_list = QListWidget()
        right.addWidget(self.sample_list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Open | QDialogButtonBox.Cancel)
        self.open_button = buttons.button(QDialogButtonBox.Open)
        self.open_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.populate_suggested_locations(start_path)
        initial_path = start_path if start_path and start_path.exists() else Path.home()
        self.select_folder(initial_path)

    def populate_suggested_locations(self, start_path: Path | None) -> None:
        suggestions: list[tuple[str, Path]] = []
        seen: set[str] = set()

        def add(label: str, path: Path | None) -> None:
            if path is None:
                return
            try:
                resolved = str(path.resolve())
            except Exception:
                resolved = str(path)
            if resolved in seen or not path.exists() or not path.is_dir():
                return
            seen.add(resolved)
            suggestions.append((label, path))

        add("Current selection", start_path)
        parent = Path(__file__).resolve().parents[4]
        add("This repository", parent)
        add("This repository / processed_data", parent / "processed_data")
        sibling_rag = parent.parent / "Podcast-RAG-pipeline"
        add("Sibling Podcast-RAG-pipeline", sibling_rag)
        add("Sibling Podcast-RAG-pipeline / processed_data", sibling_rag / "processed_data")

        if start_path:
            add("Parent of current selection", start_path.parent)

        self.location_list.clear()
        for label, path in suggestions:
            item = QListWidgetItem(f"{label}: {path}")
            item.setData(Qt.UserRole, str(path))
            self.location_list.addItem(item)

    def handle_location_selected(self) -> None:
        item = self.location_list.currentItem()
        if not item:
            return
        path = Path(str(item.data(Qt.UserRole)))
        self.select_folder(path)

    def handle_tree_selection_changed(self) -> None:
        index = self.tree_view.currentIndex()
        if not index.isValid():
            return
        folder = Path(self.fs_model.filePath(index))
        self.select_folder(folder, update_tree=False)

    def select_folder(self, folder: Path, update_tree: bool = True) -> None:
        if not folder.exists():
            return
        if update_tree:
            index = self.fs_model.index(str(folder))
            if index.isValid():
                self.tree_view.setCurrentIndex(index)
                self.tree_view.scrollTo(index)
        self.update_preview(folder)

    def update_preview(self, folder: Path) -> None:
        self.selected_path = folder
        self.path_edit.setText(str(folder))
        self.status_label.setText("Scanning folder...")
        self.detail_label.setText("Looking for *.processed_documents.json files...")
        self.metrics_label.setText("")
        self.sample_list.clear()
        self.open_button.setEnabled(False)
        self._preview_timer.start(120)

    def refresh_preview(self) -> None:
        folder = self.selected_path
        if folder is None:
            return
        files = sorted(folder.rglob("*.processed_documents.json"))
        compatible_count = len(files)

        if not files:
            self.status_label.setText("No compatible processed episode files were found in this folder.")
            self.detail_label.setText(
                "Expected pattern: **/*.processed_documents.json. "
                "Choose the processed_data folder from Podcast-RAG-pipeline or one of its parents."
            )
            self.metrics_label.setText("")
            self.open_button.setEnabled(False)
            return

        sample_names = [path.name for path in files[:12]]
        for name in sample_names:
            self.sample_list.addItem(name)

        date_tokens: list[str] = []
        cleaned_count = 0
        for path in files:
            stem = path.stem
            if "_cleaned_" in stem:
                cleaned_count += 1
            compact = "".join(ch for ch in stem if ch.isdigit())
            if len(compact) >= 8:
                date_tokens.append(compact[:8])

        extra = ""
        if len(files) > len(sample_names):
            extra = f" Showing the first {len(sample_names)} file names."
        self.status_label.setText(f"Found {compatible_count} compatible processed episode file(s).")
        self.detail_label.setText("This folder looks usable for Chroma DB Import." + extra)
        if date_tokens:
            pretty_dates = sorted(date_tokens)
            metrics = (
                f"Date range: {pretty_dates[0]} -> {pretty_dates[-1]} | "
                f"Cleaned caches: {cleaned_count} | Original caches: {compatible_count - cleaned_count}"
            )
        else:
            metrics = f"Cleaned caches: {cleaned_count} | Original caches: {compatible_count - cleaned_count}"
        self.metrics_label.setText(metrics)
        self.open_button.setEnabled(True)

class MainWindow(QMainWindow):
    """Main desktop workflow for planning, validating, and exporting Chroma datasets."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Chroma DB Import")
        self.resize(1280, 800)
        self.project_root = Path(__file__).resolve().parents[2]
        self.ui_state_path = self.project_root / "state" / "ui_state.json"
        self.context_catalog_path = self.project_root / "state" / "context_catalog.sqlite3"
        self.context_catalog = ManagedCatalog(self.context_catalog_path)
        self.context_selected_partition = ""
        self._loading_state = False

        self.loader = EpisodeLoader()
        self.processed_data_dir: Path | None = None
        self.output_root: Path | None = None
        self.episodes: list[Episode] = []
        self.included_speakers_by_episode: dict[str, set[str]] = {}
        self.thread: QThread | None = None
        self.worker: ChromaExportWorker | None = None
        self.managed_worker: ManagedImportWorker | None = None
        self.cuda_thread: QThread | None = None
        self.cuda_worker: CudaTorchInstallWorker | None = None
        self.redundancy_thread: QThread | None = None
        self.redundancy_worker: RedundancyWorker | None = None

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self.render_selected_item)

        self.right = QScrollArea()
        self.right.setWidgetResizable(True)

        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(self.right)
        splitter.setSizes([330, 950])
        self.setCentralWidget(splitter)

        self.podcast_name = QLineEdit("Podcast Chat Export")
        self.database_id = QLineEdit("podcast-chat-export")
        self.collection_name = QLineEdit("whisper_rag_v2")
        self.embedding_model = QLineEdit("BAAI/bge-large-en-v1.5")
        self.embedding_device = QComboBox()
        self.contextualization = QComboBox()
        self.contextualization.addItems(["minimal", "full", "none"])
        self.experimental_bge_m3 = QCheckBox("Use pinned BGE-M3 dense shadow profile")
        self.dedup_profile = QComboBox()
        self.dedup_profile.addItems(["off", "safe", "audit"])
        self.dedup_near_enabled = QCheckBox("Report near repetitions")
        self.dedup_near_enabled.setChecked(True)
        self.dedup_retrieval_enabled = QCheckBox("Collapse exact repetitions during retrieval")
        self.dedup_retrieval_enabled.setChecked(True)
        self.dedup_near_threshold = QLineEdit("0.90")
        self.dedup_near_length_ratio = QLineEdit("0.90")
        self.dedup_near_max_block = QLineEdit("2000")
        self.mirror_removals = QCheckBox("Mirror records removed from source caches")
        self.gpu_status = QLabel()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(180)
        self.progress_label = QLabel("Idle")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.cuda_install_button: QPushButton | None = None

        self.populate_embedding_devices()
        self.connect_prerequisite_signals()
        self._build_toolbar()
        self.load_persistent_state()
        self.update_action_states()
        self.render_global()

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)

        open_action = QAction("Open", self)
        self.set_action_help(
            open_action,
            "Choose the processed RAG output folder containing *.processed_documents.json files. "
            "Each matching file is treated as one episode.",
        )
        open_action.triggered.connect(self.open_processed_folder)
        toolbar.addAction(open_action)

        contexts_action = QAction("Contexts", self)
        self.set_action_help(
            contexts_action,
            "Discover Podcast-RAG handoffs and manage one isolated Chroma database per podcast or meeting context.",
        )
        contexts_action.triggered.connect(self.show_contexts)
        toolbar.addAction(contexts_action)

        redundancy_action = QAction("Redundancy", self)
        self.set_action_help(
            redundancy_action,
            "Configure and run a read-only semantic redundancy assessment for the selected managed context. "
            "Assessment bundles are advisory and never change the active export.",
        )
        redundancy_action.triggered.connect(self.show_redundancy)
        toolbar.addAction(redundancy_action)

        output_action = QAction("Output", self)
        self.set_action_help(
            output_action,
            "Choose the parent folder where the app will create a self-contained podcast export "
            "folder containing the Chroma DB files and podcast.json metadata.",
        )
        output_action.triggered.connect(self.choose_output_folder)
        toolbar.addAction(output_action)

        self.generate_action = QAction("Generate", self)
        self.set_action_help(
            self.generate_action,
            "Build the selected podcast export from scratch. If the export folder already exists, "
            "you will be prompted before it is deleted and rebuilt.",
        )
        self.generate_action.triggered.connect(self.generate)
        toolbar.addAction(self.generate_action)

        self.update_action = QAction("Update", self)
        self.set_action_help(
            self.update_action,
            "Append only episodes that are not already recorded in the existing podcast.json. "
            "Previously imported source files are skipped; use Generate to rebuild changed episodes.",
        )
        self.update_action.triggered.connect(self.update)
        toolbar.addAction(self.update_action)

        self.reconcile_action = QAction("Reconcile", self)
        self.set_action_help(
            self.reconcile_action,
            "Preview and explicitly remove records no longer eligible because source episodes or speaker selection changed.",
        )
        self.reconcile_action.triggered.connect(self.reconcile)
        toolbar.addAction(self.reconcile_action)

        dry_run_action = QAction("Dry Run", self)
        self.set_action_help(
            dry_run_action,
            "Validate selections and show expected document counts without writing to Chroma.",
        )
        dry_run_action.triggered.connect(self.dry_run)
        toolbar.addAction(dry_run_action)

        validation_action = QAction("Validate", self)
        self.set_action_help(
            validation_action,
            "Run processed-cache and podcast metadata validation without importing.",
        )
        validation_action.triggered.connect(self.validation_report)
        toolbar.addAction(validation_action)

        collection_info_action = QAction("Collection Info", self)
        self.set_action_help(
            collection_info_action,
            "Inspect the selected export folder metadata, manifest, and collection settings.",
        )
        collection_info_action.triggered.connect(self.collection_info)
        toolbar.addAction(collection_info_action)

        open_export_action = QAction("Open Export", self)
        self.set_action_help(open_export_action, "Open the selected export folder in Explorer.")
        open_export_action.triggered.connect(self.open_export_folder)
        toolbar.addAction(open_export_action)

        settings_action = QAction("Settings", self)
        self.set_action_help(
            settings_action,
            "Return to the Global Settings panel where you can edit Podcast name, Database ID, "
            "Collection, Embedding model/device, paths, and global speaker selections.",
        )
        settings_action.triggered.connect(self.show_global_settings)
        toolbar.addAction(settings_action)

        guide_action = QAction("Guide", self)
        self.set_action_help(
            guide_action,
            "Show the common workflows for creating a new vector DB export or updating an existing one.",
        )
        guide_action.triggered.connect(self.show_workflow_guide)
        toolbar.addAction(guide_action)

    def set_action_help(self, action: QAction, text: str) -> None:
        action.setToolTip(text)
        action.setStatusTip(text)
        action.setWhatsThis(text)

    def connect_prerequisite_signals(self) -> None:
        for field in (self.podcast_name, self.database_id, self.collection_name, self.embedding_model):
            field.textChanged.connect(self.handle_persistent_state_changed)
        self.embedding_device.currentIndexChanged.connect(self.handle_persistent_state_changed)

    def handle_persistent_state_changed(self) -> None:
        self.update_action_states()
        self.save_persistent_state()

    def populate_embedding_devices(self, selected: str = "auto") -> None:
        options, diagnostic = embedding_device_options()
        self.embedding_device.blockSignals(True)
        self.embedding_device.clear()
        for option in options:
            self.embedding_device.addItem(option.label, option.value)
        self.embedding_device.blockSignals(False)
        self.select_embedding_device(selected)
        self.gpu_status.setText(diagnostic)
        self.gpu_status.setWordWrap(True)
        self.update_action_states()

    def select_embedding_device(self, selected: str) -> None:
        value = normalize_embedding_device_value(selected)
        for index in range(self.embedding_device.count()):
            if self.embedding_device.itemData(index) == value:
                self.embedding_device.setCurrentIndex(index)
                return
        if value.startswith("cuda"):
            self.log.appendPlainText(f"Saved GPU device '{selected}' is not currently available to PyTorch; using Auto.")
        self.embedding_device.setCurrentIndex(0)

    def selected_embedding_device(self) -> str:
        return str(self.embedding_device.currentData() or "auto")

    def missing_prerequisites(self) -> list[str]:
        missing: list[str] = []
        if not self.processed_data_dir:
            missing.append("choose a processed RAG output folder")
        if not self.output_root:
            missing.append("choose an output folder")
        if not self.episodes:
            missing.append("load at least one processed episode file")
        if not self.podcast_name.text().strip():
            missing.append("enter a podcast name")
        if not self.collection_name.text().strip():
            missing.append("enter a Chroma collection")
        if not self.embedding_model.text().strip():
            missing.append("enter an embedding model")
        return missing

    def update_action_states(self) -> None:
        if not hasattr(self, "generate_action"):
            return
        missing = self.missing_prerequisites()
        enabled = not missing and self.thread is None
        detail = "Ready to export." if enabled else "Requires: " + "; ".join(missing)
        self.generate_action.setEnabled(enabled)
        self.generate_action.setToolTip(
            "Build the selected podcast export from scratch. " + detail
        )
        self.update_action.setEnabled(enabled)
        self.update_action.setToolTip(
            "Append only episodes not already recorded in the existing podcast.json. " + detail
        )
        if self.cuda_install_button:
            has_cuda_option = any(
                str(self.embedding_device.itemData(index)).startswith("cuda")
                for index in range(self.embedding_device.count())
            )
            has_working_cuda = has_cuda_option or pytorch_cuda_is_available()
            install_enabled = (not has_working_cuda) and self.cuda_thread is None and self.thread is None
            self.cuda_install_button.setEnabled(install_enabled)
            if has_working_cuda:
                self.cuda_install_button.setToolTip("CUDA is already available to PyTorch in this environment.")
            elif self.cuda_thread is not None:
                self.cuda_install_button.setToolTip("CUDA-enabled PyTorch is currently being installed.")
            else:
                self.cuda_install_button.setToolTip(
                    "Install CUDA-enabled PyTorch into this Python environment using the PyTorch CUDA 12.8 wheel index."
                )

    def open_processed_folder(self) -> None:
        start_path = self.processed_data_dir or self.project_root
        dialog = ProcessedFolderPreviewDialog(self, start_path)
        if dialog.exec() != QDialog.Accepted or not dialog.selected_path:
            return
        self.processed_data_dir = dialog.selected_path
        self.episodes = self.loader.load_folder(self.processed_data_dir)
        self.included_speakers_by_episode = {
            episode.fingerprint: set(episode.speakers) for episode in self.episodes
        }
        if self.episodes:
            self.podcast_name.setText(self.processed_data_dir.parent.name or "Podcast Export")
            self.database_id.setText(slugify(self.podcast_name.text()))
        self.rebuild_tree()
        self.render_global()
        self.update_action_states()
        self.save_persistent_state()
        self.log.appendPlainText(
            f"Loaded {len(self.episodes)} processed episode file(s) from {self.processed_data_dir}."
        )
        if hasattr(self, "reconcile_action"):
            self.reconcile_action.setEnabled(enabled)
            self.reconcile_action.setToolTip("Explicitly reconcile removals after a review. " + detail)

    def choose_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if not folder:
            return
        self.output_root = Path(folder)
        self.log.appendPlainText(f"Output folder: {self.output_root}")
        self.rebuild_tree()
        self.update_action_states()
        self.save_persistent_state()

    def show_global_settings(self) -> None:
        root = self.tree.topLevelItem(0)
        if root:
            self.tree.setCurrentItem(root)
        self.render_global()

    def rebuild_tree(self, select_key: tuple[str, str] | None = None) -> None:
        if select_key is None:
            current = self.tree.currentItem()
            select_key = current.data(0, Qt.UserRole) if current else ("global", "")
        expanded = self.expanded_tree_keys()

        self.tree.blockSignals(True)
        self.tree.clear()
        root = QTreeWidgetItem(["Global Settings"])
        root.setData(0, Qt.UserRole, ("global", ""))
        self.tree.addTopLevelItem(root)

        imported = self.imported_episode_fingerprints()
        imported_by_file = self.imported_episode_fingerprints_by_source_file()
        for episode in self.episodes:
            source_key = str(episode.path)
            if episode.fingerprint in imported:
                prefix = "[imported] "
            elif source_key in imported_by_file and imported_by_file[source_key] != episode.fingerprint:
                prefix = "[changed] "
            else:
                prefix = ""
            label = f"{prefix}{episode.episode_date or 'unknown date'} - {episode.title}"
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.UserRole, ("episode", episode.fingerprint))
            root.addChild(item)

        item_by_key = self.tree_items_by_key()
        root.setExpanded(("global", "") in expanded or not expanded)
        for key, item in item_by_key.items():
            if key in expanded:
                item.setExpanded(True)
        selected = item_by_key.get(select_key, root)
        self.tree.setCurrentItem(selected)
        self.tree.blockSignals(False)
        self.render_selected_item(selected)

    def expanded_tree_keys(self) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        root = self.tree.topLevelItem(0)
        if root and root.isExpanded():
            keys.add(root.data(0, Qt.UserRole))
        return keys

    def tree_items_by_key(self) -> dict[tuple[str, str], QTreeWidgetItem]:
        items: dict[tuple[str, str], QTreeWidgetItem] = {}
        root = self.tree.topLevelItem(0)
        if not root:
            return items
        items[root.data(0, Qt.UserRole)] = root
        for index in range(root.childCount()):
            child = root.child(index)
            items[child.data(0, Qt.UserRole)] = child
        return items

    def imported_episode_fingerprints(self) -> set[str]:
        if not self.output_root:
            return set()
        metadata_path = self.output_root / safe_folder_name(self.podcast_name.text()) / "podcast.json"
        if not metadata_path.exists():
            return set()
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return set()
        return {
            str(item.get("source_fingerprint"))
            for item in metadata.get("episodes", [])
            if item.get("source_fingerprint")
        }

    def imported_episode_fingerprints_by_source_file(self) -> dict[str, str]:
        if not self.output_root:
            return {}
        metadata_path = self.output_root / safe_folder_name(self.podcast_name.text()) / "podcast.json"
        if not metadata_path.exists():
            return {}
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return {
            str(item.get("source_file")): str(item.get("source_fingerprint"))
            for item in metadata.get("episodes", [])
            if item.get("source_file") and item.get("source_fingerprint")
        }

    def render_selected_item(self, item: QTreeWidgetItem | None) -> None:
        if not item:
            return
        kind, key = item.data(0, Qt.UserRole)
        if kind == "episode":
            episode = next((episode for episode in self.episodes if episode.fingerprint == key), None)
            if episode:
                self.render_episode(episode)
            return
        self.render_global()

    def render_global(self) -> None:
        scroll_value = self.right.verticalScrollBar().value()
        panel = QWidget()
        layout = QVBoxLayout(panel)

        form = QFormLayout()
        self.add_info_row(
            form,
            "Podcast name",
            self.podcast_name,
            "User-facing name for this export. It is also used as the child folder name under the "
            "selected output folder. Changing it for an update points the update at a different "
            "export folder unless the resulting folder name is the same.",
        )
        self.add_info_row(
            form,
            "Database ID",
            self.database_id,
            "Stable identifier written to podcast.json for the Podcast Chat app. Keep this stable "
            "for an existing export; changing it makes the export look like a different database "
            "to downstream tools.",
        )
        self.add_info_row(
            form,
            "Collection",
            self.collection_name,
            "Name of the Chroma collection that receives the imported documents. Podcast Chat reads "
            "this value from metadata. Changing it for an existing export can create or target a "
            "different Chroma collection.",
        )
        self.add_info_row(
            form,
            "Embedding model",
            self.embedding_model,
            "Embedding model used while writing vectors into Chroma. The chat/query side must use "
            "compatible embeddings. If you change this for an existing database, use Generate to "
            "rebuild the export rather than Update.",
        )
        self.add_info_row(
            form,
            "Embedding device",
            self.embedding_device,
            "Compute device used by the embedding model. Auto chooses the first usable PyTorch CUDA "
            "device when one is available, otherwise CPU. GPU names appear only when PyTorch reports "
            "them as CUDA-usable. Changing this affects import speed, not retrieval semantics.",
        )
        self.add_info_row(
            form,
            "GPU status",
            self.gpu_status,
            "Explains what PyTorch reports about CUDA/GPU availability. If Auto falls back to CPU, "
            "this status and GPU Details explain the likely reason.",
        )
        self.add_info_row(
            form,
            "Processed folder",
            QLabel(str(self.processed_data_dir or "Not selected")),
            "Source folder scanned by Open. The app searches this folder and subfolders for "
            "*.processed_documents.json files, with one file treated as one episode.",
        )
        self.add_info_row(
            form,
            "Output folder",
            QLabel(str(self.output_root or "Not selected")),
            "Parent folder chosen by Output. The generated podcast folder, Chroma files, and "
            "podcast.json metadata are written under this location.",
        )
        self.add_info_row(
            form,
            "Episodes",
            QLabel(str(len(self.episodes))),
            "Number of processed episode JSON files currently loaded from the source folder.",
        )
        self.add_info_row(
            form,
            "Date range",
            QLabel(self.global_date_range()),
            "Earliest and latest episode dates found in the loaded source files. This range is "
            "written to podcast.json so Podcast Chat can describe and filter the export.",
        )
        self.add_info_row(
            form,
            "Documents",
            QLabel(str(sum(len(episode.documents) for episode in self.episodes))),
            "Total processed RAG documents loaded before speaker filtering. Speaker selections "
            "determine which speaker-scoped documents are inserted.",
        )
        layout.addLayout(form)

        settings_buttons = QHBoxLayout()
        save_settings = QPushButton("Save Settings")
        save_settings.setToolTip(
            "Save paths, database fields, embedding settings, and speaker selections to a JSON settings file."
        )
        save_settings.clicked.connect(self.save_plan)
        load_settings = QPushButton("Load Settings")
        load_settings.setToolTip(
            "Load a saved settings file to restore paths, database fields, embedding settings, and speaker selections."
        )
        load_settings.clicked.connect(self.load_plan)
        gpu_details = QPushButton("GPU Details")
        gpu_details.setToolTip("Show PyTorch CUDA diagnostics used to decide whether GPU import is available.")
        gpu_details.clicked.connect(self.show_gpu_details)
        self.cuda_install_button = QPushButton("Install CUDA PyTorch")
        self.cuda_install_button.setToolTip(
            "Install CUDA-enabled PyTorch into this Python environment. Enabled only when PyTorch cannot use CUDA."
        )
        self.cuda_install_button.clicked.connect(self.install_cuda_torch)
        settings_buttons.addWidget(save_settings)
        settings_buttons.addWidget(load_settings)
        settings_buttons.addWidget(gpu_details)
        settings_buttons.addWidget(self.cuda_install_button)
        settings_buttons.addStretch(1)
        layout.addLayout(settings_buttons)
        self.update_action_states()

        layout.addWidget(QLabel("Speakers"))
        speaker_buttons = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_all.clicked.connect(lambda: self.set_all_global_speakers(True))
        select_none = QPushButton("Select None")
        select_none.clicked.connect(lambda: self.set_all_global_speakers(False))
        speaker_buttons.addWidget(select_all)
        speaker_buttons.addWidget(select_none)
        speaker_buttons.addStretch(1)
        layout.addLayout(speaker_buttons)
        for speaker in self.global_speakers():
            row = QHBoxLayout()
            checkbox = QCheckBox(speaker)
            checkbox.setTristate(True)
            checkbox.setCheckState(self.global_speaker_state(speaker))
            checkbox.clicked.connect(lambda checked, value=speaker: self.set_global_speaker(value, checked))
            row.addWidget(checkbox)
            layout.addLayout(row)

        layout.addWidget(QLabel("Import log"))
        self.add_progress_status(layout)
        layout.addWidget(self.log)
        layout.addStretch(1)
        self.set_right_panel(panel, scroll_value)

    def render_episode(self, episode: Episode) -> None:
        scroll_value = self.right.verticalScrollBar().value()
        panel = QWidget()
        layout = QVBoxLayout(panel)

        form = QFormLayout()
        self.add_info_row(
            form,
            "Title",
            QLabel(episode.title),
            "Episode title read from the processed RAG output metadata. It is written into "
            "podcast.json for browsing and update tracking.",
        )
        self.add_info_row(
            form,
            "Date",
            QLabel(episode.episode_date or "Unknown"),
            "Episode date read from the processed documents. It controls tree ordering and "
            "contributes to the export date range metadata.",
        )
        self.add_info_row(
            form,
            "File",
            QLabel(str(episode.path)),
            "The source *.processed_documents.json file for this episode. Update uses this path "
            "and its fingerprint to decide whether an episode was already imported.",
        )
        self.add_info_row(
            form,
            "Documents",
            QLabel(str(len(episode.documents))),
            "Total processed documents available in this episode before speaker filtering.",
        )
        self.add_info_row(
            form,
            "Leaf chunks",
            QLabel(str(episode.node_counts.get("leaf_chunk", 0))),
            "Speaker-level or content-level chunks that usually carry the most detailed retrieval text.",
        )
        self.add_info_row(
            form,
            "Position cards",
            QLabel(str(episode.node_counts.get("position_card", 0))),
            "Higher-level viewpoint summaries from the RAG output. Speaker filtering applies when "
            "the metadata identifies the selected speaker.",
        )
        self.add_info_row(
            form,
            "Cluster summaries",
            QLabel(str(episode.node_counts.get("cluster_summary", 0))),
            "Summary nodes from clustered content. Broad, non-speaker-specific summaries are "
            "preserved for retrieval quality.",
        )
        self.add_info_row(
            form,
            "Episode thesis",
            QLabel(str(episode.node_counts.get("episode_thesis", 0))),
            "Episode-level summary nodes. These are preserved even when individual speakers are omitted.",
        )
        self.add_info_row(
            form,
            "Included documents",
            QLabel(str(len(self.selected_documents(episode)))),
            "Documents that will be inserted for this episode after applying speaker selections. "
            "Omitted speaker-specific documents are left out of both Chroma and metadata.",
        )
        layout.addLayout(form)

        layout.addWidget(QLabel("Speakers"))
        included = self.included_speakers_by_episode.setdefault(episode.fingerprint, set(episode.speakers))
        for speaker in episode.speakers:
            checkbox = QCheckBox(speaker)
            checkbox.setChecked(speaker in included)
            checkbox.toggled.connect(
                lambda checked, ep=episode, value=speaker: self.set_episode_speaker(ep, value, checked)
            )
            layout.addWidget(checkbox)

        speaker_buttons = QHBoxLayout()
        select_all = QPushButton("Select All")
        select_all.clicked.connect(lambda: self.set_all_episode_speakers(episode, True))
        select_none = QPushButton("Select None")
        select_none.clicked.connect(lambda: self.set_all_episode_speakers(episode, False))
        speaker_buttons.addWidget(select_all)
        speaker_buttons.addWidget(select_none)
        speaker_buttons.addStretch(1)
        layout.addLayout(speaker_buttons)

        layout.addWidget(QLabel("Import log"))
        self.add_progress_status(layout)
        layout.addWidget(self.log)
        layout.addStretch(1)
        self.set_right_panel(panel, scroll_value)

    def add_progress_status(self, layout: QVBoxLayout) -> None:
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)

    def scroll_progress_into_view(self) -> None:
        def _scroll() -> None:
            target = max(0, self.progress_label.y() - 24)
            self.right.verticalScrollBar().setValue(target)

        QTimer.singleShot(0, _scroll)

    def set_right_panel(self, panel: QWidget, scroll_value: int = 0) -> None:
        old_panel = self.right.takeWidget()
        if old_panel:
            preserved_widgets = (
                self.podcast_name,
                self.database_id,
                self.collection_name,
                self.embedding_model,
                self.embedding_device,
                self.gpu_status,
                self.progress_label,
                self.progress_bar,
                self.log,
            )
            if getattr(self, "redundancy_cancel_button", None) is not None:
                preserved_widgets += (self.redundancy_cancel_button,)
            for widget in preserved_widgets:
                if is_descendant_of(widget, old_panel):
                    widget.setParent(None)
            old_panel.deleteLater()
        self.right.setWidget(panel)
        QTimer.singleShot(0, lambda value=scroll_value: self.right.verticalScrollBar().setValue(value))

    def add_info_row(
        self,
        form: QFormLayout,
        label_text: str,
        field_widget: QWidget,
        help_text: str,
    ) -> None:
        label_container = QWidget()
        label_layout = QHBoxLayout(label_container)
        label_layout.setContentsMargins(0, 0, 0, 0)
        label_layout.setSpacing(6)
        label_layout.addWidget(QLabel(label_text))

        info_button = QPushButton("i")
        info_button.setObjectName("infoButton")
        info_button.setFixedSize(22, 22)
        info_button.setToolTip(f"Explain {label_text}")
        info_button.clicked.connect(
            lambda _checked=False, title=label_text, text=help_text: self.show_field_help(title, text)
        )
        label_layout.addWidget(info_button)
        label_layout.addStretch(1)

        form.addRow(label_container, field_widget)

    def show_field_help(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def show_copyable_message(self, title: str, text: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(900, 420)

        layout = QVBoxLayout(dialog)
        message = QLabel(
            "You can select and copy the full message below. Use Copy to Clipboard for the entire text."
        )
        self.add_info_row(form, "Context header", self.contextualization, "Experimental embedding-only context header. Minimal is the safe default; full adds topic and hierarchy labels.")
        self.add_info_row(form, "BGE-M3 shadow", self.experimental_bge_m3, "Build with the experimental dense BGE-M3 profile without replacing an existing baseline export.")
        self.add_info_row(form, "Mirror removals", self.mirror_removals, "Preview and remove only records previously tagged to the same source cache. Confirmation is required on Update.")
        message.setWordWrap(True)
        layout.addWidget(message)

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        layout.addWidget(editor, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        copy_button = buttons.addButton("Copy to Clipboard", QDialogButtonBox.ActionRole)
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(text))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.exec()

    def build_state_payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "podcast_name": self.podcast_name.text(),
            "database_id": self.database_id.text(),
            "processed_data_dir": str(self.processed_data_dir or ""),
            "output_root": str(self.output_root or ""),
            "collection_name": self.collection_name.text(),
            "embedding_model": self.embedding_model.text(),
            "embedding_device": self.selected_embedding_device(),
            "included_speakers_by_episode": {
                fingerprint: sorted(speakers)
                for fingerprint, speakers in self.included_speakers_by_episode.items()
            },
        }

    def apply_state_payload(self, payload: dict[str, Any]) -> None:
        processed = Path(str(payload.get("processed_data_dir") or ""))
        if processed.exists():
            self.processed_data_dir = processed
            self.episodes = self.loader.load_folder(processed)
        else:
            self.processed_data_dir = None
            self.episodes = []

        output_root = Path(str(payload.get("output_root") or ""))
        self.output_root = output_root if output_root.exists() else None
        self.podcast_name.setText(str(payload.get("podcast_name") or "Podcast Chat Export"))
        self.database_id.setText(str(payload.get("database_id") or slugify(self.podcast_name.text())))
        self.collection_name.setText(str(payload.get("collection_name") or "whisper_rag_v2"))
        self.embedding_model.setText(str(payload.get("embedding_model") or "BAAI/bge-large-en-v1.5"))
        self.populate_embedding_devices(str(payload.get("embedding_device") or "auto"))
        self.included_speakers_by_episode = {
            str(fingerprint): set(speakers)
            for fingerprint, speakers in dict(payload.get("included_speakers_by_episode") or {}).items()
        }
        for episode in self.episodes:
            self.included_speakers_by_episode.setdefault(episode.fingerprint, set(episode.speakers))
        self.rebuild_tree(("global", ""))
        self.update_action_states()

    def load_persistent_state(self) -> None:
        if not self.ui_state_path.exists():
            return
        try:
            payload = json.loads(self.ui_state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.log.appendPlainText(f"Failed to load persistent UI state: {exc}")
            return
        self._loading_state = True
        try:
            self.apply_state_payload(payload)
        finally:
            self._loading_state = False
        self.log.appendPlainText(f"Loaded persistent UI state: {self.ui_state_path}")

    def save_persistent_state(self) -> None:
        if self._loading_state:
            return
        try:
            self.ui_state_path.parent.mkdir(parents=True, exist_ok=True)
            self.ui_state_path.write_text(
                json.dumps(self.build_state_payload(), indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
        except Exception as exc:
            self.log.appendPlainText(f"Failed to save persistent UI state: {exc}")

    def show_workflow_guide(self) -> None:
        QMessageBox.information(
            self,
            "Chroma DB Import Guide",
            "Create a new vector DB export:\n\n"
            "1. Click Open and choose the processed_data folder from the RAG pipeline output.\n"
            "2. Review the Global Settings, especially Podcast name, Database ID, Collection, "
            "Embedding model, and Embedding device.\n"
            "3. Use the Global Settings speaker checkboxes to include or omit speakers across "
            "all episodes, or select an episode to adjust speakers just for that episode.\n"
            "4. Click Output and choose the parent folder where the self-contained export folder "
            "should be created.\n"
            "5. Click Generate. If the target podcast folder already exists, the app asks before "
            "deleting and rebuilding it.\n\n"
            "Update an existing export:\n\n"
            "1. Open the folder containing the latest processed episode JSON files.\n"
            "2. Choose the same Podcast name and Output folder used by the existing export.\n"
            "3. Click Update. The app reads the existing podcast.json and skips source files "
            "already recorded there. New episode files are appended to the existing Chroma DB "
            "and metadata.\n\n"
            "Save Settings stores the current paths, database fields, embedding settings, and "
            "speaker selections in a JSON file. Load Settings restores those choices when you want "
            "to repeat a build, continue later, or run the same update workflow again.",
        )

    def show_gpu_details(self) -> None:
        _options, diagnostic = embedding_device_options()
        QMessageBox.information(self, "GPU Details", diagnostic)

    def install_cuda_torch(self) -> None:
        if self.cuda_thread is not None:
            return
        response = QMessageBox.question(
            self,
            "Install CUDA PyTorch?",
            "Install CUDA-enabled PyTorch into this app's current Python environment?\n\n"
            "This can download several GB from download.pytorch.org and may take a few minutes.",
        )
        if response != QMessageBox.Yes:
            return
        self.log.appendPlainText(f"Installing CUDA PyTorch from {TORCH_CUDA_INDEX_URL}")
        self.progress_label.setText("Installing CUDA-enabled PyTorch...")
        self.progress_bar.setRange(0, 0)
        self.cuda_thread = QThread()
        self.cuda_worker = CudaTorchInstallWorker()
        self.cuda_worker.moveToThread(self.cuda_thread)
        self.cuda_thread.started.connect(self.cuda_worker.run)
        self.cuda_worker.progress.connect(self.handle_cuda_install_progress)
        self.cuda_worker.finished.connect(self.handle_cuda_install_finished)
        self.cuda_worker.failed.connect(self.handle_cuda_install_failed)
        self.cuda_worker.finished.connect(self.cuda_thread.quit)
        self.cuda_worker.failed.connect(self.cuda_thread.quit)
        self.cuda_worker.finished.connect(self.cuda_worker.deleteLater)
        self.cuda_worker.failed.connect(self.cuda_worker.deleteLater)
        self.cuda_thread.finished.connect(self.cuda_thread.deleteLater)
        self.cuda_thread.finished.connect(self.clear_cuda_worker)
        self.update_action_states()
        self.cuda_thread.start()

    def handle_cuda_install_progress(self, message: str) -> None:
        self.log.appendPlainText("[CUDA install] " + message)
        self.progress_label.setText(message)

    def handle_cuda_install_finished(self, message: str) -> None:
        self.log.appendPlainText("[CUDA install] " + message)
        self.populate_embedding_devices(self.selected_embedding_device())
        _options, diagnostic = embedding_device_options()
        self.log.appendPlainText("[CUDA install] " + diagnostic)
        self.progress_label.setText("CUDA PyTorch install complete")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)

    def handle_cuda_install_failed(self, message: str) -> None:
        self.log.appendPlainText("[CUDA install failed] " + message)
        self.progress_label.setText("CUDA PyTorch install failed")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.show_copyable_message("CUDA install failed", message)

    def clear_cuda_worker(self) -> None:
        self.cuda_thread = None
        self.cuda_worker = None
        self.update_action_states()

    def global_speakers(self) -> list[str]:
        return sorted({speaker for episode in self.episodes for speaker in episode.speakers})

    def global_date_range(self) -> str:
        dates = [episode.episode_date for episode in self.episodes if episode.episode_date]
        if not dates:
            return "Unknown"
        return f"{min(dates)} to {max(dates)}"

    def global_speaker_state(self, speaker: str) -> Qt.CheckState:
        relevant = [episode for episode in self.episodes if speaker in episode.speakers]
        if not relevant:
            return Qt.Unchecked
        checked = [
            speaker in self.included_speakers_by_episode.get(episode.fingerprint, set())
            for episode in relevant
        ]
        if all(checked):
            return Qt.Checked
        if any(checked):
            return Qt.PartiallyChecked
        return Qt.Unchecked

    def set_global_speaker(self, speaker: str, enabled: bool) -> None:
        for episode in self.episodes:
            if speaker not in episode.speakers:
                continue
            included = self.included_speakers_by_episode.setdefault(episode.fingerprint, set())
            if enabled:
                included.add(speaker)
            else:
                included.discard(speaker)
        self.render_global()

    def show_contexts(self) -> None:
        self.render_contexts()

    def _redundancy_release_ids(self) -> list[str]:
        if not self.context_selected_partition:
            return []
        return [str(row.get("upstream_release_id") or "") for row in self.context_catalog.releases(self.context_selected_partition) if row.get("upstream_release_id")]

    def show_redundancy(self) -> None:
        if not self.context_selected_partition:
            QMessageBox.information(self, "Context required", "Select a managed context before opening semantic redundancy.")
            return
        try:
            policy_record = self.context_catalog.get_redundancy_policy(self.context_selected_partition)
            policy = dict(policy_record["policy"])
            judge_config = self.context_catalog.get_redundancy_judge_config(self.context_selected_partition) or {}
        except Exception as exc:
            QMessageBox.warning(self, "Redundancy unavailable", f"{type(exc).__name__}: {exc}")
            return
        releases = self._redundancy_release_ids()
        if not releases:
            QMessageBox.information(self, "No release", "This context has no discovered release to assess.")
            return

        panel = QWidget()
        layout = QVBoxLayout(panel)
        title = QLabel("Semantic Redundancy")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        intro = QLabel(
            "Assess candidate overlap and optional local-judge relationships in a private bundle. "
            "Every original occurrence remains in the base export; saving a policy here does not activate retrieval changes in Podcast Chat."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.redundancy_release = QComboBox()
        self.redundancy_release.addItems(releases)
        self.redundancy_storage = QComboBox()
        self.redundancy_storage.addItems(["full", "shared_input"])
        self.redundancy_storage.setCurrentText(str(policy.get("vector_storage") or "full"))
        self.redundancy_retrieval = QComboBox()
        self.redundancy_retrieval.addItems(["ranked", "semantic_mmr"])
        self.redundancy_retrieval.setCurrentText(str(policy.get("retrieval_mode") or "ranked"))
        self.redundancy_judge_enabled = QCheckBox("Allow explicit judge pilot")
        self.redundancy_judge_enabled.setChecked(bool(policy.get("judge_enabled")))
        self.redundancy_judge_fraction = QLineEdit(str(policy.get("judge_record_fraction", "0.10")))
        self.redundancy_judge_max_calls = QLineEdit(str(policy.get("judge_max_calls", "100")))
        self.redundancy_base_url = QLineEdit(str(judge_config.get("base_url") or "http://localhost:1234/v1"))
        self.redundancy_model = QLineEdit(str(judge_config.get("model") or ""))
        self.redundancy_model_fingerprint = QLineEdit(str(judge_config.get("model_fingerprint") or ""))
        self.add_info_row(form, "Base release", self.redundancy_release, "The immutable managed release that will be inspected. Assessment never changes its active pointer.")
        self.add_info_row(form, "Vector storage", self.redundancy_storage, "Choose full vectors or shared-input representatives for the private assessment artifact. Original evidence stays complete in either mode.")
        self.add_info_row(form, "Retrieval recommendation", self.redundancy_retrieval, "Record ranked or semantic MMR as an advisory preference. The consumer must explicitly select a validated bundle.")
        self.add_info_row(form, "Judge enablement", self.redundancy_judge_enabled, "Saving this only permits a later explicit Pilot judge action; it never calls a model by itself.")
        self.add_info_row(form, "Judge fraction", self.redundancy_judge_fraction, "Fraction of eligible candidates to send to the bounded local judge, subject to the call cap.")
        self.add_info_row(form, "Judge max calls", self.redundancy_judge_max_calls, "Hard maximum number of judge requests for one assessment job.")
        self.add_info_row(form, "LM Studio URL", self.redundancy_base_url, "Loopback-compatible LM Studio endpoint. Credentials are not stored in the catalog.")
        self.add_info_row(form, "LM Studio model", self.redundancy_model, "Explicit model ID selected by the user; the importer never auto-selects or downloads a model.")
        self.add_info_row(form, "Model fingerprint", self.redundancy_model_fingerprint, "Optional immutable model artifact identity used to scope judgment cache entries.")
        layout.addLayout(form)

        channels = QHBoxLayout()
        self.redundancy_channel_checks: dict[str, QCheckBox] = {}
        for name, label in (("lexical", "Lexical"), ("structural", "Source / structure"), ("dense", "Dense")):
            checkbox = QCheckBox(label)
            checkbox.setChecked(bool(policy.get(f"{name}_enabled", True)))
            self.redundancy_channel_checks[name] = checkbox
            channels.addWidget(checkbox)
        channels.addStretch(1)
        layout.addWidget(QLabel("Candidate channels"))
        layout.addLayout(channels)

        resume_row = QHBoxLayout()
        self.redundancy_resume_job = QLineEdit()
        self.redundancy_resume_job.setPlaceholderText("Optional frozen job ID")
        resume_row.addWidget(self.redundancy_resume_job, 1)
        layout.addLayout(resume_row)

        config_buttons = QHBoxLayout()
        save_policy = QPushButton("Save Policy")
        save_policy.clicked.connect(self.save_redundancy_policy_from_ui)
        save_judge = QPushButton("Save Judge Configuration")
        save_judge.clicked.connect(self.save_redundancy_judge_from_ui)
        config_buttons.addWidget(save_policy)
        config_buttons.addWidget(save_judge)
        config_buttons.addStretch(1)
        layout.addLayout(config_buttons)

        action_buttons = QHBoxLayout()
        preview = QPushButton("Preview")
        preview.setToolTip("Compute model-free lexical/source coverage without creating a job or bundle.")
        preview.clicked.connect(self.preview_redundancy)
        assess = QPushButton("Assess")
        assess.setToolTip("Run the selected candidate channels without invoking the judge.")
        assess.clicked.connect(self.assess_redundancy)
        pilot = QPushButton("Pilot Judge")
        pilot.setToolTip("Run the explicitly configured, bounded local judge for this frozen assessment.")
        pilot.clicked.connect(self.pilot_redundancy)
        resume = QPushButton("Resume")
        resume.clicked.connect(self.resume_redundancy)
        self.redundancy_cancel_button = QPushButton("Cancel")
        self.redundancy_cancel_button.setEnabled(False)
        self.redundancy_cancel_button.clicked.connect(self.cancel_redundancy)
        action_buttons.addWidget(preview)
        action_buttons.addWidget(assess)
        action_buttons.addWidget(pilot)
        action_buttons.addWidget(resume)
        action_buttons.addWidget(self.redundancy_cancel_button)
        layout.addLayout(action_buttons)

        artifact_buttons = QHBoxLayout()
        review = QPushButton("Review Bundle")
        review.clicked.connect(self.review_redundancy_bundle)
        export_labels = QPushButton("Export Labels")
        export_labels.clicked.connect(self.export_redundancy_labels)
        evaluate = QPushButton("Evaluate")
        evaluate.clicked.connect(self.evaluate_redundancy_bundle)
        artifact_buttons.addWidget(review)
        artifact_buttons.addWidget(export_labels)
        artifact_buttons.addWidget(evaluate)
        artifact_buttons.addStretch(1)
        layout.addLayout(artifact_buttons)

        layout.addWidget(QLabel("Operation log"))
        self.add_progress_status(layout)
        layout.addWidget(self.log)
        layout.addStretch(1)
        self.set_right_panel(panel)

    def _redundancy_common_args(self, command: str) -> list[str]:
        release = str(self.redundancy_release.currentData() or self.redundancy_release.currentText() or "")
        return [command, "--catalog", str(self.context_catalog_path), "--partition", self.context_selected_partition, "--release", release]

    def _selected_redundancy_channels(self) -> list[str]:
        return [name for name in ("lexical", "structural", "dense") if self.redundancy_channel_checks[name].isChecked()]

    def save_redundancy_policy_from_ui(self) -> None:
        try:
            current = self.context_catalog.get_redundancy_policy(self.context_selected_partition)
            policy = dict(current["policy"])
            policy.update({
                "vector_storage": self.redundancy_storage.currentText(),
                "retrieval_mode": self.redundancy_retrieval.currentText(),
                "judge_enabled": self.redundancy_judge_enabled.isChecked(),
                "judge_record_fraction": float(self.redundancy_judge_fraction.text()),
                "judge_max_calls": int(self.redundancy_judge_max_calls.text()),
            })
            fingerprint = self.context_catalog.save_redundancy_policy(self.context_selected_partition, policy)
            self.log.appendPlainText(f"Saved semantic redundancy policy: {fingerprint}")
            QMessageBox.information(self, "Policy saved", "The validated policy was saved as a new scoped revision. Existing jobs remain frozen.")
        except Exception as exc:
            QMessageBox.warning(self, "Policy not saved", f"{type(exc).__name__}: {exc}")

    def save_redundancy_judge_from_ui(self) -> bool:
        try:
            self.context_catalog.set_redundancy_judge_config(self.context_selected_partition, {
                "base_url": self.redundancy_base_url.text().strip(),
                "model": self.redundancy_model.text().strip(),
                "model_fingerprint": self.redundancy_model_fingerprint.text().strip() or None,
            })
            self.log.appendPlainText(f"Saved explicit judge configuration for {self.context_selected_partition}")
            QMessageBox.information(self, "Judge configuration saved", "The model ID is stored privately for this partition. The importer will not call it until Pilot Judge is pressed.")
            return True
        except Exception as exc:
            QMessageBox.warning(self, "Judge configuration not saved", f"{type(exc).__name__}: {exc}")
            return False

    def _launch_redundancy_worker(self, arguments: list[str], description: str) -> None:
        if self.redundancy_thread is not None:
            QMessageBox.information(self, "Operation running", "Wait for the current semantic redundancy operation to finish.")
            return
        self.redundancy_thread = QThread()
        self.redundancy_worker = RedundancyWorker(arguments)
        self.redundancy_worker.moveToThread(self.redundancy_thread)
        self.redundancy_thread.started.connect(self.redundancy_worker.run)
        self.redundancy_worker.progress.connect(lambda message: self.log.appendPlainText(f"[Redundancy] {message}"))
        self.redundancy_worker.finished.connect(self.handle_redundancy_finished)
        self.redundancy_worker.failed.connect(self.handle_redundancy_failed)
        self.redundancy_worker.finished.connect(self.redundancy_thread.quit)
        self.redundancy_worker.failed.connect(self.redundancy_thread.quit)
        self.redundancy_worker.finished.connect(self.redundancy_worker.deleteLater)
        self.redundancy_worker.failed.connect(self.redundancy_worker.deleteLater)
        self.redundancy_thread.finished.connect(self.redundancy_thread.deleteLater)
        self.redundancy_thread.finished.connect(self.clear_redundancy_worker)
        self.progress_label.setText(description)
        self.progress_bar.setRange(0, 0)
        if hasattr(self, "redundancy_cancel_button"):
            self.redundancy_cancel_button.setEnabled(True)
        self.redundancy_thread.start()

    def cancel_redundancy(self) -> None:
        if self.redundancy_worker is None:
            return
        self.redundancy_worker.cancel()
        self.progress_label.setText("Cancelling redundancy operation...")

    def clear_redundancy_worker(self) -> None:
        self.redundancy_thread = None
        self.redundancy_worker = None
        if hasattr(self, "redundancy_cancel_button"):
            self.redundancy_cancel_button.setEnabled(False)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)

    def handle_redundancy_finished(self, result: dict[str, Any]) -> None:
        output = str(result.get("output") or "")
        if output:
            self.log.appendPlainText(output)
        partial = int(result.get("exit_code") or 0) == 2
        self.progress_label.setText("Redundancy operation completed with pending coverage" if partial else "Redundancy operation complete")
        if partial:
            QMessageBox.warning(self, "Redundancy assessment is partial", "The bundle was preserved, but one or more requested channels or judge calls were unavailable. Review the coverage before using it.")
        else:
            QMessageBox.information(self, "Redundancy operation complete", "The read-only operation completed. Review the bundle or report before making any consumer selection.")

    def handle_redundancy_failed(self, message: str) -> None:
        self.progress_label.setText("Redundancy operation failed")
        self.log.appendPlainText(f"[Redundancy failed] {message}")
        QMessageBox.warning(self, "Redundancy operation failed", message)

    def preview_redundancy(self) -> None:
        self._launch_redundancy_worker(self._redundancy_common_args("preview"), "Computing model-free redundancy preview...")

    def assess_redundancy(self) -> None:
        channels = self._selected_redundancy_channels()
        if not channels:
            QMessageBox.warning(self, "Channel required", "Select at least one candidate channel.")
            return
        arguments = self._redundancy_common_args("assess")
        arguments.extend(["--channels", ",".join(channels)])
        self._launch_redundancy_worker(arguments, "Running read-only redundancy assessment...")

    def pilot_redundancy(self) -> None:
        if not self.redundancy_judge_enabled.isChecked():
            QMessageBox.warning(self, "Judge not enabled", "Save the policy with explicit judge enablement before starting a pilot.")
            return
        try:
            saved_policy = self.context_catalog.get_redundancy_policy(self.context_selected_partition)
        except Exception as exc:
            QMessageBox.warning(self, "Judge policy unavailable", f"{type(exc).__name__}: {exc}")
            return
        if not bool(saved_policy.get("policy", {}).get("judge_enabled")):
            QMessageBox.warning(self, "Judge policy not saved", "Save the policy with explicit judge enablement before starting a pilot.")
            return
        if not self.save_redundancy_judge_from_ui():
            return
        channels = self._selected_redundancy_channels()
        if not channels:
            QMessageBox.warning(self, "Channel required", "Select at least one candidate channel.")
            return
        arguments = self._redundancy_common_args("assess")
        arguments.extend(["--channels", ",".join(channels), "--judge"])
        self._launch_redundancy_worker(arguments, "Running bounded local judge pilot...")

    def resume_redundancy(self) -> None:
        job_id = self.redundancy_resume_job.text().strip()
        if not job_id:
            QMessageBox.warning(self, "Job required", "Enter a frozen job ID before choosing Resume.")
            return
        arguments = self._redundancy_common_args("assess")
        arguments.extend(["--resume", job_id])
        self._launch_redundancy_worker(arguments, "Resuming frozen redundancy assessment...")

    def review_redundancy_bundle(self) -> None:
        bundle = QFileDialog.getExistingDirectory(self, "Choose redundancy bundle")
        if bundle:
            self._launch_redundancy_worker(["review", "--bundle", bundle], "Validating redundancy bundle...")

    def export_redundancy_labels(self) -> None:
        bundle = QFileDialog.getExistingDirectory(self, "Choose redundancy bundle")
        if not bundle:
            return
        output, _selected_filter = QFileDialog.getSaveFileName(self, "Save label examples", "labels.json", "JSON files (*.json)")
        if output:
            self._launch_redundancy_worker(["label-export", "--bundle", bundle, "--output", output], "Exporting label examples...")

    def evaluate_redundancy_bundle(self) -> None:
        bundle = QFileDialog.getExistingDirectory(self, "Choose redundancy bundle")
        if not bundle:
            return
        labels, _labels_filter = QFileDialog.getOpenFileName(self, "Choose reviewed labels", "", "JSON files (*.json)")
        if not labels:
            return
        queries, _queries_filter = QFileDialog.getOpenFileName(self, "Choose queries (optional)", "", "JSON files (*.json)")
        query_results, _results_filter = QFileDialog.getOpenFileName(self, "Choose retrieval results (optional)", "", "JSON files (*.json)")
        output, _output_filter = QFileDialog.getSaveFileName(self, "Save evaluation report", "evaluation.json", "JSON files (*.json)")
        if not output:
            return
        arguments = ["evaluate", "--bundle", bundle, "--labels", labels, "--output", output]
        if queries:
            arguments.extend(["--queries", queries])
        if query_results:
            arguments.extend(["--query-results", query_results])
        self._launch_redundancy_worker(arguments, "Writing frozen redundancy evaluation...")

    def render_contexts(self) -> None:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        title = QLabel("Managed Contexts")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        intro = QLabel(
            "Each valid Podcast-RAG handoff or release becomes an independent database. "
            "The manifest supplies identity; this screen manages only local discovery, profiles, and destinations."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        buttons = QHBoxLayout()
        link = QPushButton("Link Source Root")
        link.setToolTip("Choose a Podcast-RAG project or release root once. Future contexts are discovered from contract manifests.")
        link.clicked.connect(self.link_context_source)
        refresh = QPushButton("Discover / Refresh")
        refresh.clicked.connect(self.discover_contexts)
        buttons.addWidget(link)
        buttons.addWidget(refresh)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.context_list = QListWidget()
        self.context_list.currentItemChanged.connect(self.context_selection_changed)
        layout.addWidget(self.context_list, 1)
        self.context_detail = QLabel("No context selected.")
        self.context_detail.setWordWrap(True)
        layout.addWidget(self.context_detail)

        actions = QHBoxLayout()
        archive = QPushButton("Archive / Restore")
        archive.clicked.connect(self.toggle_context_archive)
        import_button = QPushButton("Import Newest Release")
        import_button.clicked.connect(self.import_selected_context)
        profile_button = QPushButton("Save Current Profile")
        profile_button.setToolTip("Save the current embedding and representation choices for the selected context in the local catalog.")
        profile_button.clicked.connect(self.save_context_profile)
        open_button = QPushButton("Open Active Database")
        open_button.clicked.connect(self.open_selected_context)
        redundancy_button = QPushButton("Semantic Redundancy")
        redundancy_button.setToolTip("Configure and run a read-only redundancy assessment for this context.")
        redundancy_button.clicked.connect(self.show_redundancy)
        actions.addWidget(archive)
        actions.addWidget(profile_button)
        actions.addWidget(import_button)
        actions.addWidget(open_button)
        actions.addWidget(redundancy_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        dedup_form = QFormLayout()
        dedup_form.addRow("Dedup profile", self.dedup_profile)
        dedup_form.addRow("Near-match report", self.dedup_near_enabled)
        dedup_form.addRow("Retrieval repetition control", self.dedup_retrieval_enabled)
        dedup_form.addRow("Near Jaccard threshold", self.dedup_near_threshold)
        dedup_form.addRow("Near length ratio", self.dedup_near_length_ratio)
        dedup_form.addRow("Near block limit", self.dedup_near_max_block)
        layout.addLayout(dedup_form)
        review_button = QPushButton("Review Deduplication")
        review_button.setToolTip("Read the validated release ledger and show groups, reasons, and coverage.")
        review_button.clicked.connect(self.review_selected_context)
        layout.addWidget(review_button)
        preview_button = QPushButton("Preview Next Import")
        preview_button.setToolTip("Validate the selected release and compute its complete prospective dedup plan without embedding or activation.")
        preview_button.clicked.connect(self.preview_selected_context)
        layout.addWidget(preview_button)
        self.set_right_panel(panel)
        self.refresh_context_list()

    def refresh_context_list(self) -> None:
        if not hasattr(self, "context_list"):
            return
        self.context_list.blockSignals(True)
        self.context_list.clear()
        selected = self.context_selected_partition
        for context in self.context_catalog.contexts():
            status = context.get("local_status") or context.get("producer_status") or "active"
            label = f"{context.get('display_name')}  [{context.get('context_type')}] — {status}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(context.get("partition_id")))
            self.context_list.addItem(item)
            if str(context.get("partition_id")) == selected:
                self.context_list.setCurrentItem(item)
        self.context_list.blockSignals(False)
        if self.context_list.currentItem() is None and self.context_list.count():
            self.context_list.setCurrentRow(0)
        self.context_selection_changed(self.context_list.currentItem(), None)

    def link_context_source(self) -> None:
        root = QFileDialog.getExistingDirectory(self, "Link Podcast-RAG source root")
        if not root:
            return
        try:
            self.context_catalog.add_source_root(Path(root))
            self.context_catalog.set_setting("managed_output_root", str((self.output_root or (self.project_root / "exports")).resolve()))
            report = discover(self.context_catalog)
            self.context_selected_partition = ""
            self.refresh_context_list()
            self.log.appendPlainText(f"Discovered {len(report['releases'])} release(s), {len(report['invalid'])} invalid candidate(s).")
            if report["invalid"]:
                QMessageBox.warning(self, "Discovery warnings", f"{len(report['invalid'])} manifest candidate(s) were rejected. Review the context status or use the CLI for details.")
        except Exception as exc:
            QMessageBox.critical(self, "Source link failed", f"{type(exc).__name__}: {exc}")

    def discover_contexts(self) -> None:
        try:
            report = discover(self.context_catalog)
            self.refresh_context_list()
            self.log.appendPlainText(f"Context discovery complete: {len(report['contexts'])} context record(s), {len(report['releases'])} release(s).")
        except Exception as exc:
            QMessageBox.critical(self, "Discovery failed", f"{type(exc).__name__}: {exc}")

    def context_selection_changed(self, item: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if item is None:
            self.context_detail.setText("No context selected.")
            return
        partition_id = str(item.data(Qt.UserRole))
        self.context_selected_partition = partition_id
        context = self.context_catalog.context(partition_id) or {}
        releases = self.context_catalog.releases(partition_id)
        output_root = Path(self.context_catalog.get_setting("managed_output_root", str(self.project_root / "exports")))
        partition_root = output_root / "partitions" / partition_id
        active_release_id = "none"
        episode_count = document_count = 0
        active_pointer = partition_root / "active-release.json"
        if active_pointer.is_file():
            try:
                active_payload = json.loads(active_pointer.read_text(encoding="utf-8"))
                active_release_id = str(active_payload.get("release_id") or "none")
                metadata_path = partition_root / "releases" / active_release_id / "export" / "podcast.json"
                if metadata_path.is_file():
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    episode_count = int(metadata.get("episode_count") or len(metadata.get("episodes") or []))
                    document_count = int(metadata.get("document_count") or 0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                active_release_id = "invalid pointer"
        latest_run = self.context_catalog.latest_run(partition_id)
        warning_count = sum(1 for release in releases if release.get("status") in {"quarantined", "failed"})
        lines = [
            f"Display name: {context.get('display_name')}",
            f"Partition: {context.get('partition_id')}",
            f"Corpus: {context.get('corpus_id')}",
            f"Workflow: {context.get('workflow_profile')}",
            f"Discovered releases: {len(releases)}",
            f"Active release: {active_release_id}",
            f"Active counts: {episode_count} episode(s), {document_count} document(s)",
            f"Last import result: {latest_run.get('status') if latest_run else 'not imported'}",
            f"Validation warnings: {warning_count} quarantined/failed release(s)",
            f"Database root: {partition_root}",
            "Portability: managed export; machine-specific paths are masked in portable metadata",
        ]
        if releases:
            lines.append(f"Newest release: {releases[0].get('upstream_release_id')} ({releases[0].get('status')})")
        profile = self.context_catalog.profile(partition_id)
        if profile:
            lines.append(f"Saved import profile: {profile.get('profile_fingerprint')}")
            saved_policy = resolve_dedup_policy((profile.get("profile") or {}).get("dedup_policy"), default_profile="safe")
            lines.extend([
                f"Deduplication: {saved_policy['profile']} (near={'on' if saved_policy['near_enabled'] else 'off'}, retrieval={'on' if saved_policy['retrieval']['enabled'] else 'off'})",
                f"Profile changes apply on the next import; Safe/Audit require Chat dedup-aliases-v1 support.",
            ])
            self._load_context_profile_controls(profile["profile"])
        self.context_detail.setText("\n".join(lines))

    def _load_context_profile_controls(self, profile: dict[str, Any]) -> None:
        policy = resolve_dedup_policy(profile.get("dedup_policy"), default_profile="safe")
        for widget, value in ((self.dedup_profile, policy["profile"]),):
            widget.blockSignals(True)
            widget.setCurrentText(value)
            widget.blockSignals(False)
        self.dedup_near_enabled.setChecked(policy["near_enabled"])
        self.dedup_retrieval_enabled.setChecked(policy["retrieval"]["enabled"])
        self.dedup_near_threshold.setText(str(policy["near_jaccard_threshold"]))
        self.dedup_near_length_ratio.setText(str(policy["near_length_ratio"]))
        self.dedup_near_max_block.setText(str(policy["near_max_block_records"]))

    def save_context_profile(self) -> None:
        if not self.context_selected_partition:
            return
        selected_speakers = sorted(
            {
                speaker
                for episode in self.episodes
                for speaker in self.included_speakers_by_episode.get(episode.fingerprint, set())
            }
        ) or None
        fingerprint = self.context_catalog.save_profile(
            self.context_selected_partition,
            import_profile_payload(
                ImportConfig(
                    embedding_model=self.embedding_model.text().strip() or "BAAI/bge-large-en-v1.5",
                    embedding_device=self.selected_embedding_device(),
                    contextualization=self.contextualization.currentText(),
                    experimental_bge_m3=self.experimental_bge_m3.isChecked(),
                    selected_speakers=selected_speakers,
                    dedup_policy=resolve_dedup_policy({
                        "profile": self.dedup_profile.currentText(),
                        "near_enabled": self.dedup_near_enabled.isChecked(),
                        "near_jaccard_threshold": float(self.dedup_near_threshold.text() or "0.90"),
                        "near_length_ratio": float(self.dedup_near_length_ratio.text() or "0.90"),
                        "near_max_block_records": int(self.dedup_near_max_block.text() or "2000"),
                        "retrieval": {"enabled": self.dedup_retrieval_enabled.isChecked()},
                    }, default_profile="safe"),
                )
            ),
        )
        self.log.appendPlainText(f"Saved managed import profile for {self.context_selected_partition}: {fingerprint}")
        self.context_selection_changed(self.context_list.currentItem(), None)

    def review_selected_context(self) -> None:
        if not self.context_selected_partition:
            return
        output_root = Path(self.context_catalog.get_setting("managed_output_root", str(self.project_root / "exports")))
        partition_root = output_root / "partitions" / self.context_selected_partition
        pointer_path = partition_root / "active-release.json"
        if not pointer_path.is_file():
            QMessageBox.information(self, "Deduplication review", "This context has no active release.")
            return
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            export = partition_root / "releases" / str(pointer["release_id"]) / "export"
            release = json.loads((export / "release.json").read_text(encoding="utf-8"))
            result = validate_dedup_artifacts(export, release)
            counts = result["manifest"].get("counts") or {}
            QMessageBox.information(self, "Deduplication review", "\n".join([
                f"Stored: {counts.get('stored', 0)}",
                f"Suppressed exact: {counts.get('suppressed_exact', 0)}",
                f"Would suppress (Audit): {counts.get('would_suppress_exact', 0)}",
                f"Exact groups: {result.get('exact_group_count', 0)}",
                f"Near edges: {result.get('near_edge_count', 0)}",
                f"Coverage: {result['manifest'].get('near', {}).get('status', 'unknown')}",
            ]))
        except Exception as exc:
            QMessageBox.warning(self, "Deduplication review unavailable", f"{type(exc).__name__}: {exc}")

    def preview_selected_context(self) -> None:
        if not self.context_selected_partition or self.thread is not None:
            return
        self.thread = QThread()
        self.managed_worker = ManagedDedupPreviewWorker(
            ImportConfig(), self.project_root, self.context_catalog_path,
            self.context_selected_partition, self.output_root,
        )
        self.managed_worker.moveToThread(self.thread)
        self.thread.started.connect(self.managed_worker.run)
        self.managed_worker.finished.connect(self.handle_dedup_preview)
        self.managed_worker.failed.connect(self.handle_failed)
        self.managed_worker.finished.connect(self.thread.quit)
        self.managed_worker.failed.connect(self.thread.quit)
        self.managed_worker.finished.connect(self.managed_worker.deleteLater)
        self.managed_worker.failed.connect(self.managed_worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self.clear_worker)
        self.progress_label.setText("Computing deduplication preview...")
        self.progress_bar.setRange(0, 0)
        self.thread.start()

    def handle_dedup_preview(self, result: dict[str, Any]) -> None:
        counts = result.get("dedup") or {}
        self.log.appendPlainText("Prospective dedup plan: " + json.dumps(counts, ensure_ascii=True, default=str))
        self.progress_label.setText("Deduplication preview complete")
        QMessageBox.information(self, "Deduplication preview", "\n".join([
            f"Eligible: {counts.get('eligible', 0)}",
            f"Stored: {counts.get('stored', 0)}",
            f"Suppressed exact: {counts.get('suppressed_exact', 0)}",
            f"Would suppress: {counts.get('would_suppress_exact', 0)}",
            f"Exact groups: {counts.get('exact_group_count', 0)}",
            f"Plan fingerprint: {result.get('plan_fingerprint', 'unavailable')}",
            "No embeddings, catalog settings, or active release were changed.",
        ]))

    def toggle_context_archive(self) -> None:
        if not self.context_selected_partition:
            return
        context = self.context_catalog.context(self.context_selected_partition) or {}
        current = str(context.get("local_status") or "active")
        self.context_catalog.set_local_status(self.context_selected_partition, "active" if current in {"archived", "hidden"} else "archived")
        self.refresh_context_list()

    def import_selected_context(self) -> None:
        if not self.context_selected_partition:
            QMessageBox.information(self, "Context required", "Select a context first.")
            return
        if self.thread is not None:
            return
        config = ImportConfig(
            embedding_model=self.embedding_model.text().strip() or "BAAI/bge-large-en-v1.5",
            embedding_device=self.selected_embedding_device(),
            contextualization=self.contextualization.currentText(),
            experimental_bge_m3=self.experimental_bge_m3.isChecked(),
            selected_speakers=sorted(
                {
                    speaker
                    for episode in self.episodes
                    for speaker in self.included_speakers_by_episode.get(episode.fingerprint, set())
                }
            ) or None,
        )
        self.thread = QThread()
        self.managed_worker = ManagedImportWorker(config, self.project_root, self.context_catalog_path, self.context_selected_partition, self.output_root)
        self.managed_worker.moveToThread(self.thread)
        self.thread.started.connect(self.managed_worker.run)
        self.managed_worker.progress.connect(self.handle_progress)
        self.managed_worker.finished.connect(self.handle_managed_finished)
        self.managed_worker.failed.connect(self.handle_failed)
        self.managed_worker.finished.connect(self.thread.quit)
        self.managed_worker.failed.connect(self.thread.quit)
        self.managed_worker.finished.connect(self.managed_worker.deleteLater)
        self.managed_worker.failed.connect(self.managed_worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self.clear_worker)
        self.progress_label.setText("Importing managed context...")
        self.progress_bar.setRange(0, 0)
        self.thread.start()

    def open_selected_context(self) -> None:
        if not self.context_selected_partition:
            return
        root = Path(self.context_catalog.get_setting("managed_output_root", str(self.project_root / "exports"))) / "partitions" / self.context_selected_partition
        active = root / "active-release.json"
        if active.exists():
            payload = json.loads(active.read_text(encoding="utf-8"))
            release_root = root / "releases" / str(payload.get("release_id")) / "export"
            if release_root.is_dir():
                subprocess.Popen(["explorer", str(release_root)])
                return
        QMessageBox.information(self, "Database unavailable", "This context has no active imported release yet.")
        self.save_persistent_state()

    def set_all_global_speakers(self, enabled: bool) -> None:
        for episode in self.episodes:
            included = self.included_speakers_by_episode.setdefault(episode.fingerprint, set())
            if enabled:
                included.update(episode.speakers)
            else:
                included.clear()
        self.render_global()
        self.save_persistent_state()

    def set_episode_speaker(self, episode: Episode, speaker: str, checked: bool) -> None:
        included = self.included_speakers_by_episode.setdefault(episode.fingerprint, set())
        if checked:
            included.add(speaker)
        else:
            included.discard(speaker)
        self.render_episode(episode)
        self.save_persistent_state()

    def set_all_episode_speakers(self, episode: Episode, enabled: bool) -> None:
        included = self.included_speakers_by_episode.setdefault(episode.fingerprint, set())
        if enabled:
            included.update(episode.speakers)
        else:
            included.clear()
        self.render_episode(episode)
        self.save_persistent_state()

    def selected_documents(self, episode: Episode) -> list[ProcessedDocument]:
        included_speakers = self.included_speakers_by_episode.get(episode.fingerprint, set())
        return [doc for doc in episode.documents if should_include_document(doc, included_speakers)]

    def build_plan(self) -> ImportPlan | None:
        if not self.processed_data_dir:
            QMessageBox.warning(self, "Processed folder required", "Choose a processed RAG output folder first.")
            return None
        if not self.output_root:
            QMessageBox.warning(self, "Output folder required", "Choose an output folder first.")
            return None
        podcast_name = self.podcast_name.text().strip()
        if not podcast_name:
            QMessageBox.warning(self, "Podcast name required", "Enter a podcast name.")
            return None
        database_id = self.database_id.text().strip() or slugify(podcast_name)
        return ImportPlan(
            podcast_name=podcast_name,
            database_id=database_id,
            processed_data_dir=self.processed_data_dir,
            output_root=self.output_root,
            collection_name=self.collection_name.text().strip() or "whisper_rag_v2",
            embedding_model=self.embedding_model.text().strip() or "BAAI/bge-large-en-v1.5",
            embedding_device=self.selected_embedding_device(),
            contextualization=self.contextualization.currentText(),
            experimental_bge_m3=self.experimental_bge_m3.isChecked(),
            allow_delete_missing=self.mirror_removals.isChecked(),
            reconcile=self.mirror_removals.isChecked(),
            episodes=self.episodes,
            included_speakers_by_episode=self.included_speakers_by_episode,
        )

    def generate(self) -> None:
        plan = self.build_plan()
        if not plan:
            return
        if plan.export_dir.exists():
            response = QMessageBox.question(
                self,
                "Rebuild output?",
                f"{plan.export_dir} already exists. Delete and rebuild it?",
            )
            if response != QMessageBox.Yes:
                return
        self.start_export(plan, "generate")

    def update(self) -> None:
        plan = self.build_plan()
        if not plan:
            return
        try:
            preview = preview_ui_reconciliation(plan)
        except Exception as exc:
            QMessageBox.critical(self, "Update preview failed", f"No changes were made.\n\n{type(exc).__name__}: {exc}")
            return
        removed_preview = "\n".join(f"- {item}" for item in preview.removed[:20])
        if len(preview.removed) > 20:
            removed_preview += f"\n- ... and {len(preview.removed) - 20} more"
        message = (
            f"Added: {len(preview.added)}\nChanged: {len(preview.changed)}\n"
            f"Metadata only: {len(preview.metadata_only)}\nUnchanged: {len(preview.unchanged)}\n"
            f"Removed: {len(preview.removed)}"
        )
        if preview.removed:
            message += f"\n\nRecords proposed for removal:\n{removed_preview}"
            if not plan.allow_delete_missing:
                QMessageBox.warning(
                    self,
                    "Update preview",
                    message + "\n\nThese records will be retained by the default update. Enable Mirror removals and run Update again only to reconcile them away.",
                )
        if QMessageBox.question(self, "Confirm update preview", message + "\n\nApply this update?") != QMessageBox.Yes:
            return
        self.start_export(plan, "update")

    def reconcile(self) -> None:
        plan = self.build_plan()
        if not plan:
            return
        plan.allow_delete_missing = True
        plan.reconcile = True
        try:
            preview = preview_ui_import_plan(plan, "reconcile")
        except Exception as exc:
            QMessageBox.critical(self, "Reconcile preview failed", f"No changes were made.\n\n{type(exc).__name__}: {exc}")
            return
        reconciliation = preview["reconciliation"]
        message = (
            f"Added: {len(reconciliation['added'])}\n"
            f"Changed: {len(reconciliation['changed'])}\n"
            f"Unchanged: {len(reconciliation['unchanged'])}\n"
            f"Delete: {len(reconciliation['removed'])}\n"
            f"Embedding: {preview['embedding']['compatibility']}\n"
            f"Staging: {preview['staging_destination']}\n\n"
            "Apply this destructive reconciliation?"
        )
        if QMessageBox.question(self, "Confirm reconciliation", message) != QMessageBox.Yes:
            return
        self.start_export(plan, "reconcile")

    def dry_run(self) -> None:
        plan = self.build_plan()
        if not plan:
            return
        report = preview_ui_import_plan(plan, "update")
        summary = report["validation"]["summary"]
        QMessageBox.information(
            self,
            "Dry Run Summary",
            "\n".join(
                [
                    f"Eligible records: {report['eligible_records']}",
                    f"Source episodes: {report['source_episodes']}",
                    f"Invalid records: {report['invalid_records']}",
                    f"Validation: {'PASS' if report['validation']['valid'] else 'FAIL'}",
                    f"Added / changed / unchanged / removed: {len(report['reconciliation']['added'])} / {len(report['reconciliation']['changed'])} / {len(report['reconciliation']['unchanged'])} / {len(report['reconciliation']['removed'])}",
                    f"Embedding: {report['embedding']['compatibility']} ({report['embedding']['identity'][:12]})",
                    f"Staging: {report['staging_destination']}",
                    f"Date range: {summary.get('date_min') or 'unknown'} to {summary.get('date_max') or 'unknown'}",
                    f"Errors: {summary['error_count']}",
                    f"Warnings: {summary['warning_count']}",
                ]
            ),
        )
        self.log.appendPlainText("Dry run complete: " + json.dumps(summary, ensure_ascii=True))

    def validation_report(self) -> None:
        plan = self.build_plan()
        if not plan:
            return
        report = build_ui_validation_report(plan)
        lines = [
            f"Valid: {report['valid']}",
            f"Documents: {report['summary']['document_count']}",
            f"Errors: {report['summary']['error_count']}",
            f"Warnings: {report['summary']['warning_count']}",
        ]
        for file_report in report["files"][:10]:
            if file_report["errors"]:
                lines.append(f"{Path(file_report['path']).name}: {file_report['errors'][0]}")
            elif file_report["warnings"]:
                lines.append(f"{Path(file_report['path']).name}: {file_report['warnings'][0]}")
        QMessageBox.information(self, "Validation Report", "\n".join(lines))
        self.log.appendPlainText("Validation report: " + json.dumps(report["summary"], ensure_ascii=True))

    def collection_info(self) -> None:
        plan = self.build_plan()
        if not plan:
            return
        manifest_path = plan.export_dir / "import_manifest.json"
        metadata_path = plan.export_dir / "podcast.json"
        lines = [f"Export folder: {plan.export_dir}", f"Collection: {plan.collection_name}"]
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            lines.extend(
                [
                    f"Manifest version: {manifest.get('manifest_version')}",
                    f"Importer version: {manifest.get('importer_version')}",
                    f"Embedding model: {manifest.get('embedding_model')}",
                    f"Embedding dimension: {manifest.get('embedding_dimension')}",
                    f"Source files: {len(manifest.get('source_files', []))}",
                ]
            )
        else:
            lines.append("Manifest: not found")
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            report = validate_podcast_metadata(metadata)
            lines.extend([f"podcast.json valid: {report.valid}", f"Episodes: {len(metadata.get('episodes', []))}"])
        else:
            lines.append("podcast.json: not found")
        QMessageBox.information(self, "Collection Info", "\n".join(lines))

    def open_export_folder(self) -> None:
        plan = self.build_plan()
        if not plan:
            return
        plan.export_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(plan.export_dir)])

    def save_plan(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Save import settings", filter="JSON files (*.json)")
        if not filename:
            return
        Path(filename).write_text(json.dumps(self.build_state_payload(), indent=2, ensure_ascii=True), encoding="utf-8")
        self.log.appendPlainText(f"Saved settings: {filename}")

    def load_plan(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Load import settings", filter="JSON files (*.json)")
        if not filename:
            return
        payload = json.loads(Path(filename).read_text(encoding="utf-8"))
        self._loading_state = True
        try:
            self.apply_state_payload(payload)
        finally:
            self._loading_state = False
        self.save_persistent_state()
        self.log.appendPlainText(f"Loaded settings: {filename}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.save_persistent_state()
        self.context_catalog.close()
        super().closeEvent(event)

    def start_export(self, plan: ImportPlan, mode: str) -> None:
        if self.thread is not None:
            return
        self.log.appendPlainText(f"Starting {mode}: {plan.export_dir}")
        self.progress_label.setText(f"Starting {mode}...")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("Working...")
        self.thread = QThread()
        self.update_action_states()
        self.worker = ChromaExportWorker(plan, mode)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.handle_progress)
        self.worker.finished.connect(self.handle_finished)
        self.worker.failed.connect(self.handle_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.failed.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self.clear_worker)
        self.thread.start()
        self.scroll_progress_into_view()

    def handle_progress(self, progress: ImportProgress) -> None:
        prefix = f"[{progress.current}/{progress.total}] " if progress.total else ""
        self.log.appendPlainText(prefix + progress.message)
        self.progress_label.setText(progress.message)
        if progress.total:
            self.progress_bar.setRange(0, progress.total)
            self.progress_bar.setValue(min(progress.current, progress.total))
            self.progress_bar.setFormat(f"{min(progress.current, progress.total)} / {progress.total}")
        else:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("Working...")

    def handle_finished(self, summary: ImportSummary) -> None:
        self.log.appendPlainText(
            f"Complete: imported_episodes={summary.imported_episodes}, "
            f"skipped_episodes={summary.skipped_episodes}, inserted={summary.inserted}, "
            f"skipped_documents={summary.skipped_documents}, elapsed={summary.elapsed_seconds}s"
        )
        self.progress_label.setText(
            f"Complete: imported {summary.imported_episodes}, skipped {summary.skipped_episodes}, inserted {summary.inserted}"
        )
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.progress_bar.setFormat("Complete")
        QMessageBox.information(
            self,
            "Import Summary",
            "\n".join(
                [
                    f"Inserted documents: {summary.inserted}",
                    f"Skipped documents: {summary.skipped_documents}",
                    f"Imported episodes: {summary.imported_episodes}",
                    f"Skipped episodes: {summary.skipped_episodes}",
                    f"Elapsed seconds: {summary.elapsed_seconds}",
                    f"Warnings: {len(summary.warnings)}",
                ]
            ),
        )
        self.rebuild_tree()

    def handle_managed_finished(self, result: dict[str, Any]) -> None:
        status = str(result.get("status") or "unknown")
        self.log.appendPlainText("Managed context result: " + json.dumps(result, ensure_ascii=True, default=str))
        self.progress_label.setText(f"Managed context: {status}")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1 if status in {"completed", "reused"} else 0)
        if status in {"completed", "reused"}:
            QMessageBox.information(self, "Managed import complete", f"Database exported to:\n{result.get('export')}")
        else:
            QMessageBox.warning(self, "Managed import not completed", json.dumps(result, indent=2, default=str)[:6000])
        self.render_contexts()

    def handle_failed(self, message: str) -> None:
        self.log.appendPlainText("Failed: " + message)
        self.progress_label.setText("Failed")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Failed")
        self.show_copyable_message("Import failed", message)

    def clear_worker(self) -> None:
        self.thread = None
        self.worker = None
        self.managed_worker = None
        self.update_action_states()
