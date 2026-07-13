from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from chroma_db_import.ui_export import merge_episode_entries, update_should_skip_episode
from chroma_db_import.ui_models import Episode, ImportPlan, ImportProgress, ImportSummary, ProcessedDocument
from chroma_db_import.ui_support import apply_dark_theme
from chroma_db_import.ui_window import MainWindow

__all__ = [
    "Episode",
    "ImportPlan",
    "ImportProgress",
    "ImportSummary",
    "MainWindow",
    "ProcessedDocument",
    "merge_episode_entries",
    "update_should_skip_episode",
    "main",
]


def main() -> int:
    """Launch the desktop UI for export planning and import execution."""
    app = QApplication(sys.argv)
    apply_dark_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()
