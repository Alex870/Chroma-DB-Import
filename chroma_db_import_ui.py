from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chroma_db_import import (
    ImportConfig,
    cache_fingerprint,
    has_text,
    load_runtime_deps,
    sanitize_metadata,
    validate_documents,
)


ALWAYS_INCLUDE_NODE_TYPES = {"episode_thesis"}
SUMMARY_NODE_TYPES = {"cluster_summary"}


@dataclass
class ProcessedDocument:
    page_content: str
    metadata: dict[str, Any]


@dataclass
class Episode:
    path: Path
    fingerprint: str
    title: str
    episode_id: str
    episode_date: str
    documents: list[ProcessedDocument]
    speakers: list[str]
    node_counts: dict[str, int]

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.episode_date or "9999-99-99", self.title.lower())


@dataclass
class ImportPlan:
    podcast_name: str
    database_id: str
    processed_data_dir: Path
    output_root: Path
    collection_name: str
    embedding_model: str
    episodes: list[Episode]
    included_speakers_by_episode: dict[str, set[str]]

    @property
    def export_dir(self) -> Path:
        return self.output_root / safe_folder_name(self.podcast_name)


@dataclass
class ImportProgress:
    message: str
    current: int = 0
    total: int = 0


@dataclass
class ImportSummary:
    inserted: int
    skipped_episodes: int
    imported_episodes: int
    export_dir: Path


class EpisodeLoader:
    def load_folder(self, folder: Path) -> list[Episode]:
        files = sorted(folder.rglob("*.processed_documents.json"))
        episodes = [self.load_file(path) for path in files]
        return sorted(episodes, key=lambda episode: episode.sort_key)

    def load_file(self, path: Path) -> Episode:
        payload = json.loads(path.read_text(encoding="utf-8"))
        documents = [
            ProcessedDocument(
                page_content=str(item.get("page_content", "")),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in payload.get("documents", [])
            if isinstance(item, dict)
        ]
        speakers = sorted({speaker for doc in documents for speaker in document_speakers(doc.metadata)})
        node_counts: dict[str, int] = {}
        for doc in documents:
            node_type = str(doc.metadata.get("node_type") or "unknown")
            node_counts[node_type] = node_counts.get(node_type, 0) + 1

        first_meta = next((doc.metadata for doc in documents if doc.metadata), {})
        episode_date = str(first_present(doc.metadata.get("episode_date") for doc in documents) or "")
        title = str(
            first_present(doc.metadata.get("episode_title") for doc in documents)
            or payload.get("episode_title")
            or path.stem.replace(".processed_documents", "")
        )
        episode_id = str(first_meta.get("episode_id") or path.stem)

        return Episode(
            path=path,
            fingerprint=cache_fingerprint(path),
            title=title,
            episode_id=episode_id,
            episode_date=episode_date,
            documents=documents,
            speakers=speakers,
            node_counts=node_counts,
        )


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
            summary = export_chroma(self.plan, self.mode, self.progress.emit)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(summary)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Chroma DB Import")
        self.resize(1280, 800)

        self.loader = EpisodeLoader()
        self.processed_data_dir: Path | None = None
        self.output_root: Path | None = None
        self.episodes: list[Episode] = []
        self.included_speakers_by_episode: dict[str, set[str]] = {}
        self.thread: QThread | None = None
        self.worker: ChromaExportWorker | None = None

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
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(180)

        self._build_toolbar()
        self.render_global()

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)

        open_action = QAction("Open", self)
        open_action.triggered.connect(self.open_processed_folder)
        toolbar.addAction(open_action)

        output_action = QAction("Output", self)
        output_action.triggered.connect(self.choose_output_folder)
        toolbar.addAction(output_action)

        generate_action = QAction("Generate", self)
        generate_action.triggered.connect(self.generate)
        toolbar.addAction(generate_action)

        update_action = QAction("Update", self)
        update_action.triggered.connect(self.update)
        toolbar.addAction(update_action)

        save_plan_action = QAction("Save Plan", self)
        save_plan_action.triggered.connect(self.save_plan)
        toolbar.addAction(save_plan_action)

        load_plan_action = QAction("Load Plan", self)
        load_plan_action.triggered.connect(self.load_plan)
        toolbar.addAction(load_plan_action)

    def open_processed_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open processed RAG output folder")
        if not folder:
            return
        self.processed_data_dir = Path(folder)
        self.episodes = self.loader.load_folder(self.processed_data_dir)
        self.included_speakers_by_episode = {
            episode.fingerprint: set(episode.speakers) for episode in self.episodes
        }
        if self.episodes:
            self.podcast_name.setText(self.processed_data_dir.parent.name or "Podcast Export")
            self.database_id.setText(slugify(self.podcast_name.text()))
        self.rebuild_tree()
        self.render_global()
        self.log.appendPlainText(f"Loaded {len(self.episodes)} processed episode file(s).")

    def choose_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if not folder:
            return
        self.output_root = Path(folder)
        self.log.appendPlainText(f"Output folder: {self.output_root}")
        self.rebuild_tree()
        self.render_global()

    def rebuild_tree(self) -> None:
        self.tree.clear()
        root = QTreeWidgetItem(["Global Settings"])
        root.setData(0, Qt.UserRole, ("global", ""))
        root.setExpanded(True)
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
        self.tree.setCurrentItem(root)

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
        panel = QWidget()
        layout = QVBoxLayout(panel)

        form = QFormLayout()
        form.addRow("Podcast name", self.podcast_name)
        form.addRow("Database ID", self.database_id)
        form.addRow("Collection", self.collection_name)
        form.addRow("Embedding model", self.embedding_model)
        form.addRow("Processed folder", QLabel(str(self.processed_data_dir or "Not selected")))
        form.addRow("Output folder", QLabel(str(self.output_root or "Not selected")))
        form.addRow("Episodes", QLabel(str(len(self.episodes))))
        form.addRow("Date range", QLabel(self.global_date_range()))
        form.addRow("Documents", QLabel(str(sum(len(episode.documents) for episode in self.episodes))))
        layout.addLayout(form)

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
            checkbox.stateChanged.connect(lambda state, value=speaker: self.set_global_speaker(value, state))
            row.addWidget(checkbox)
            layout.addLayout(row)

        layout.addWidget(QLabel("Import log"))
        layout.addWidget(self.log)
        layout.addStretch(1)
        self.right.setWidget(panel)

    def render_episode(self, episode: Episode) -> None:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        form = QFormLayout()
        form.addRow("Title", QLabel(episode.title))
        form.addRow("Date", QLabel(episode.episode_date or "Unknown"))
        form.addRow("File", QLabel(str(episode.path)))
        form.addRow("Documents", QLabel(str(len(episode.documents))))
        form.addRow("Leaf chunks", QLabel(str(episode.node_counts.get("leaf_chunk", 0))))
        form.addRow("Position cards", QLabel(str(episode.node_counts.get("position_card", 0))))
        form.addRow("Cluster summaries", QLabel(str(episode.node_counts.get("cluster_summary", 0))))
        form.addRow("Episode thesis", QLabel(str(episode.node_counts.get("episode_thesis", 0))))
        form.addRow("Included documents", QLabel(str(len(self.selected_documents(episode)))))
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
        layout.addWidget(self.log)
        layout.addStretch(1)
        self.right.setWidget(panel)

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

    def set_global_speaker(self, speaker: str, state: int) -> None:
        if state == Qt.PartiallyChecked.value:
            return
        enabled = state == Qt.Checked.value
        for episode in self.episodes:
            if speaker not in episode.speakers:
                continue
            included = self.included_speakers_by_episode.setdefault(episode.fingerprint, set())
            if enabled:
                included.add(speaker)
            else:
                included.discard(speaker)
        self.render_global()

    def set_all_global_speakers(self, enabled: bool) -> None:
        for episode in self.episodes:
            included = self.included_speakers_by_episode.setdefault(episode.fingerprint, set())
            if enabled:
                included.update(episode.speakers)
            else:
                included.clear()
        self.render_global()

    def set_episode_speaker(self, episode: Episode, speaker: str, checked: bool) -> None:
        included = self.included_speakers_by_episode.setdefault(episode.fingerprint, set())
        if checked:
            included.add(speaker)
        else:
            included.discard(speaker)
        self.rebuild_tree()
        self.render_episode(episode)

    def set_all_episode_speakers(self, episode: Episode, enabled: bool) -> None:
        included = self.included_speakers_by_episode.setdefault(episode.fingerprint, set())
        if enabled:
            included.update(episode.speakers)
        else:
            included.clear()
        self.rebuild_tree()
        self.render_episode(episode)

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
        self.start_export(plan, "update")

    def save_plan(self) -> None:
        if not self.episodes:
            QMessageBox.warning(self, "No plan to save", "Open a processed RAG output folder first.")
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save import plan", filter="JSON files (*.json)")
        if not filename:
            return
        payload = {
            "version": 1,
            "podcast_name": self.podcast_name.text(),
            "database_id": self.database_id.text(),
            "processed_data_dir": str(self.processed_data_dir or ""),
            "output_root": str(self.output_root or ""),
            "collection_name": self.collection_name.text(),
            "embedding_model": self.embedding_model.text(),
            "included_speakers_by_episode": {
                fingerprint: sorted(speakers)
                for fingerprint, speakers in self.included_speakers_by_episode.items()
            },
        }
        Path(filename).write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        self.log.appendPlainText(f"Saved plan: {filename}")

    def load_plan(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Load import plan", filter="JSON files (*.json)")
        if not filename:
            return
        payload = json.loads(Path(filename).read_text(encoding="utf-8"))
        processed = Path(str(payload.get("processed_data_dir") or ""))
        if processed.exists():
            self.processed_data_dir = processed
            self.episodes = self.loader.load_folder(processed)
        output_root = Path(str(payload.get("output_root") or ""))
        self.output_root = output_root if output_root.exists() else None
        self.podcast_name.setText(str(payload.get("podcast_name") or "Podcast Chat Export"))
        self.database_id.setText(str(payload.get("database_id") or slugify(self.podcast_name.text())))
        self.collection_name.setText(str(payload.get("collection_name") or "whisper_rag_v2"))
        self.embedding_model.setText(str(payload.get("embedding_model") or "BAAI/bge-large-en-v1.5"))
        self.included_speakers_by_episode = {
            str(fingerprint): set(speakers)
            for fingerprint, speakers in dict(payload.get("included_speakers_by_episode") or {}).items()
        }
        for episode in self.episodes:
            self.included_speakers_by_episode.setdefault(episode.fingerprint, set(episode.speakers))
        self.rebuild_tree()
        self.render_global()
        self.log.appendPlainText(f"Loaded plan: {filename}")

    def start_export(self, plan: ImportPlan, mode: str) -> None:
        if self.thread is not None:
            return
        self.log.appendPlainText(f"Starting {mode}: {plan.export_dir}")
        self.thread = QThread()
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

    def handle_progress(self, progress: ImportProgress) -> None:
        prefix = f"[{progress.current}/{progress.total}] " if progress.total else ""
        self.log.appendPlainText(prefix + progress.message)

    def handle_finished(self, summary: ImportSummary) -> None:
        self.log.appendPlainText(
            f"Complete: imported_episodes={summary.imported_episodes}, "
            f"skipped_episodes={summary.skipped_episodes}, inserted={summary.inserted}"
        )
        self.rebuild_tree()

    def handle_failed(self, message: str) -> None:
        self.log.appendPlainText("Failed: " + message)
        QMessageBox.critical(self, "Import failed", message)

    def clear_worker(self) -> None:
        self.thread = None
        self.worker = None


def export_chroma(plan: ImportPlan, mode: str, emit_progress) -> ImportSummary:  # type: ignore[no-untyped-def]
    load_runtime_deps()
    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    from langchain_huggingface import HuggingFaceEmbeddings

    if mode == "generate" and plan.export_dir.exists():
        shutil.rmtree(plan.export_dir)
    plan.export_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = plan.export_dir / "podcast.json"
    existing_fingerprints = set()
    existing_source_files = set()
    existing_episode_entries: list[dict[str, Any]] = []
    if mode == "update" and metadata_path.exists():
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        existing_episode_entries = list(existing.get("episodes", []))
        existing_fingerprints = {
            str(item.get("source_fingerprint"))
            for item in existing.get("episodes", [])
            if item.get("source_fingerprint")
        }
        existing_source_files = {
            str(item.get("source_file"))
            for item in existing.get("episodes", [])
            if item.get("source_file")
        }

    embeddings = HuggingFaceEmbeddings(model_name=plan.embedding_model)
    vectorstore = Chroma(
        embedding_function=embeddings,
        persist_directory=str(plan.export_dir),
        collection_name=plan.collection_name,
    )

    imported_episodes = []
    inserted = 0
    skipped = 0
    for index, episode in enumerate(plan.episodes, 1):
        if mode == "update" and (
            episode.fingerprint in existing_fingerprints or str(episode.path) in existing_source_files
        ):
            skipped += 1
            emit_progress(ImportProgress(f"Skipped already imported: {episode.title}", index, len(plan.episodes)))
            continue

        selected = select_documents_for_episode(episode, plan.included_speakers_by_episode)
        validate_documents([Document(page_content=doc.page_content, metadata=doc.metadata) for doc in selected], str(episode.path))
        documents = [
            Document(page_content=doc.page_content, metadata=sanitize_metadata(doc.metadata))
            for doc in selected
            if has_text(doc.page_content)
        ]
        ids = [str(doc.metadata["node_id"]) for doc in selected if has_text(doc.page_content)]

        for start in range(0, len(documents), 64):
            batch_docs = documents[start : start + 64]
            batch_ids = ids[start : start + 64]
            if batch_docs:
                vectorstore.add_documents(batch_docs, ids=batch_ids)
                inserted += len(batch_docs)

        imported_episodes.append(episode_metadata_entry(episode, selected))
        emit_progress(ImportProgress(f"Imported {len(documents)} documents: {episode.title}", index, len(plan.episodes)))

    all_episode_entries = merge_episode_entries(existing_episode_entries, imported_episodes)
    total_documents = sum(int(item.get("document_count") or 0) for item in all_episode_entries)
    write_podcast_metadata(plan, all_episode_entries, total_documents, metadata_path)
    return ImportSummary(
        inserted=inserted,
        skipped_episodes=skipped,
        imported_episodes=len(imported_episodes),
        export_dir=plan.export_dir,
    )


def merge_episode_entries(
    existing_episode_entries: list[dict[str, Any]],
    imported_episodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_source_file = {
        str(entry.get("source_file")): entry
        for entry in existing_episode_entries
        if entry.get("source_file")
    }
    for entry in imported_episodes:
        by_source_file[str(entry.get("source_file"))] = entry
    return sorted(
        by_source_file.values(),
        key=lambda item: (str(item.get("episode_date") or "9999-99-99"), str(item.get("episode_title") or "")),
    )


def select_documents_for_episode(
    episode: Episode,
    included_speakers_by_episode: dict[str, set[str]],
) -> list[ProcessedDocument]:
    included_speakers = included_speakers_by_episode.get(episode.fingerprint, set())
    return [doc for doc in episode.documents if should_include_document(doc, included_speakers)]


def should_include_document(doc: ProcessedDocument, included_speakers: set[str]) -> bool:
    metadata = doc.metadata
    node_type = str(metadata.get("node_type") or "")
    speaker_scope = str(metadata.get("speaker_scope") or "")
    speakers = set(document_speakers(metadata))

    if node_type in ALWAYS_INCLUDE_NODE_TYPES:
        return True
    if node_type in SUMMARY_NODE_TYPES and speaker_scope != "single":
        return True
    if not speakers:
        return True
    return bool(speakers & included_speakers)


def episode_metadata_entry(episode: Episode, selected: list[ProcessedDocument]) -> dict[str, Any]:
    speakers = sorted({speaker for doc in selected for speaker in document_speakers(doc.metadata)})
    return {
        "source_file": str(episode.path),
        "source_fingerprint": episode.fingerprint,
        "episode_id": episode.episode_id,
        "episode_title": episode.title,
        "episode_date": episode.episode_date,
        "document_count": len(selected),
        "speakers": [{"id": slugify(speaker), "name": speaker} for speaker in speakers],
        "imported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def write_podcast_metadata(
    plan: ImportPlan,
    imported_episodes: list[dict[str, Any]],
    inserted: int,
    metadata_path: Path,
) -> None:
    all_speakers = sorted(
        {
            speaker["name"]
            for episode in imported_episodes
            for speaker in episode.get("speakers", [])
            if speaker.get("name")
        }
    )
    dates = [episode["episode_date"] for episode in imported_episodes if episode.get("episode_date")]
    payload = {
        "podcast_name": plan.podcast_name,
        "database_id": plan.database_id,
        "collection_name": plan.collection_name,
        "embedding_model": plan.embedding_model,
        "description": f"Generated from processed RAG output in {plan.processed_data_dir}",
        "date_range": {
            "start": min(dates) if dates else "",
            "end": max(dates) if dates else "",
        },
        "episode_count": len(imported_episodes),
        "chunk_count": inserted,
        "speakers": [{"id": slugify(speaker), "name": speaker} for speaker in all_speakers],
        "episodes": imported_episodes,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "generated_by": "Chroma DB Import UI",
    }
    metadata_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def document_speakers(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    speaker = metadata.get("speaker")
    if isinstance(speaker, str) and speaker.strip() and speaker.lower() not in {"unknown", "multiple", "mixed"}:
        values.append(speaker.strip())

    speakers = metadata.get("speakers")
    if isinstance(speakers, str):
        try:
            speakers = json.loads(speakers)
        except json.JSONDecodeError:
            speakers = [part.strip() for part in speakers.split(",")]
    if isinstance(speakers, list):
        for item in speakers:
            if isinstance(item, str) and item.strip() and item.lower() not in {"unknown", "multiple", "mixed"}:
                values.append(item.strip())

    return list(dict.fromkeys(values))


def first_present(values) -> Any:  # type: ignore[no-untyped-def]
    for value in values:
        if value not in (None, ""):
            return value
    return None


def slugify(value: str) -> str:
    slug = "".join(character.lower() if character.isalnum() else "-" for character in value)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "podcast"


def safe_folder_name(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value).strip()
    safe = re.sub(r"\s+", " ", safe)
    return safe or "Podcast Export"


def apply_dark_theme(app: QApplication) -> None:
    app.setStyleSheet(
        """
        QWidget {
            background: #101318;
            color: #e5e7eb;
            font-family: Segoe UI;
            font-size: 10.5pt;
        }
        QToolBar {
            background: #171b22;
            border-bottom: 1px solid #2b313d;
            padding: 6px;
            spacing: 8px;
        }
        QTreeWidget {
            background: #151922;
            border: none;
            padding: 8px;
        }
        QTreeWidget::item {
            min-height: 30px;
            border-radius: 4px;
            padding: 4px;
        }
        QTreeWidget::item:selected {
            background: #2f6feb;
            color: white;
        }
        QLabel {
            padding: 2px;
        }
        QLineEdit, QPlainTextEdit {
            background: #0c0f14;
            border: 1px solid #2b313d;
            border-radius: 6px;
            padding: 8px;
            selection-background-color: #2f6feb;
        }
        QPushButton {
            background: #2f6feb;
            border: none;
            border-radius: 6px;
            color: white;
            font-weight: 600;
            padding: 9px 16px;
        }
        QCheckBox {
            spacing: 8px;
            min-height: 26px;
        }
        QScrollArea {
            border: none;
        }
        QSplitter::handle {
            background: #2b313d;
        }
        """
    )


def main() -> int:
    app = QApplication(sys.argv)
    apply_dark_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
