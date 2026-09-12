from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser(description="Launch the Chroma DB Import desktop application.")
    parser.add_argument(
        "--workspace",
        choices=("default", "contexts"),
        default="default",
        help="Workspace to show when the application opens.",
    )
    arguments = parser.parse_args()
    app = QApplication([sys.argv[0]])
    apply_dark_theme(app)
    window = MainWindow()
    window.show()
    if arguments.workspace == "contexts":
        window.show_contexts()
    return app.exec()
