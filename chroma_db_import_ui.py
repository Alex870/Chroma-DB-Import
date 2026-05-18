"""Compatibility wrapper for the Chroma DB Import desktop UI."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from chroma_db_import.ui import (
    Episode,
    ImportPlan,
    ImportProgress,
    ImportSummary,
    MainWindow,
    ProcessedDocument,
    main,
    merge_episode_entries,
    update_should_skip_episode,
)

__all__ = [
    "Episode",
    "ImportPlan",
    "ImportProgress",
    "ImportSummary",
    "MainWindow",
    "ProcessedDocument",
    "main",
    "merge_episode_entries",
    "update_should_skip_episode",
]


if __name__ == "__main__":
    raise SystemExit(main())
