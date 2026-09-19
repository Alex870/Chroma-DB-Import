"""Bounded folder inspection and source suggestions used by Create/Settings."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from chroma_db_import.asset_filters import asset_filter_label, select_asset_files
from chroma_db_import.ui_loader import EpisodeLoader

from .models import BridgeError, SelectionPolicy
from .selection import select_documents


def _inside(path: Path, root: Path) -> bool:
    try: path.resolve().relative_to(root.resolve()); return True
    except ValueError: return False


def suggestions(current_path: str | None = None) -> list[dict[str, str]]:
    candidates: list[tuple[str, Path]] = []
    if current_path:
        current = Path(current_path).expanduser().resolve()
        candidates.append(("Current folder", current))
        candidates.append(("Processed data child", current / "processed_data"))
        if current.parent != current: candidates.append(("Sibling processed_data", current.parent / "processed_data"))
    result: list[dict[str, str]] = []; seen: set[str] = set()
    for label, path in candidates:
        try: key = os.path.normcase(str(path.resolve())).casefold()
        except OSError: continue
        if key in seen or not path.is_dir(): continue
        seen.add(key); result.append({"label": label, "path": str(path)})
    return result


def list_folders(root: str, relative_path: str | None = None) -> list[dict[str, Any]]:
    base = Path(root).expanduser().resolve()
    if not base.is_dir(): raise BridgeError("SOURCE_UNAVAILABLE", f"Folder is unavailable: {base}", field="root")
    candidate = (base / str(relative_path or "")).resolve()
    if not _inside(candidate, base) or candidate.is_symlink() or not candidate.is_dir(): raise BridgeError("VALIDATION_FAILED", "The requested folder is outside the selected root.", field="relative_path")
    rows: list[dict[str, Any]] = []
    try: children = sorted(candidate.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc: raise BridgeError("SOURCE_UNAVAILABLE", f"Folder contents could not be read: {exc}") from exc
    for child in children:
        try:
            if not child.is_dir() or child.is_symlink() or not _inside(child, base): continue
            rows.append({"name": child.name, "relative_path": str(child.relative_to(base)), "has_children": any(item.is_dir() and not item.is_symlink() for item in child.iterdir())})
        except OSError:
            rows.append({"name": child.name, "relative_path": str(child.relative_to(base)), "has_children": None, "error": "permission_denied"})
    return rows


def inspect_folder(path: str, asset_filter: str, asset_pattern: str = "") -> dict[str, Any]:
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir(): raise BridgeError("SOURCE_UNAVAILABLE", f"The source folder is unavailable: {folder}", field="path")
    files = sorted(folder.rglob("*.processed_documents.json"))
    selection = select_asset_files(files, asset_filter, asset_pattern)
    samples = [str(item.relative_to(folder)) for item in selection.selected_paths[:12]]
    excluded = [{"path": str(item.relative_to(folder)), "reason": "does_not_match_filter"} for item in selection.excluded_paths[:500]]
    invalid = [str(item.relative_to(folder)) for item in selection.invalid_paths[:500]]
    dates: list[str] = []; eligible = 0; episode_rows: list[dict[str, Any]] = []
    loader = EpisodeLoader()
    for file in selection.selected_paths:
        try:
            episode = loader.load_file(file); dates.extend([episode.episode_date] if episode.episode_date else []); included = select_documents(episode, SelectionPolicy(asset_filter=asset_filter, asset_pattern=asset_pattern)); eligible += len(included)
            episode_rows.append({"episode_id": episode.episode_id, "title": episode.title, "date": episode.episode_date or None, "source_document_count": len(episode.documents), "included_document_count": len(included), "source_file": str(file)})
        except (OSError, ValueError, json.JSONDecodeError):
            invalid.append(str(file.relative_to(folder)))
    return {"path": str(folder), "asset_filter": asset_filter, "asset_filter_label": asset_filter_label(asset_filter), "asset_pattern": asset_pattern, "discovered_count": selection.discovered_count, "selected_count": selection.selected_count, "eligible_records": eligible, "excluded_count": len(selection.excluded_paths), "excluded_files": excluded, "invalid_count": len(invalid), "invalid_files": invalid, "sample_files": samples, "date_range": {"start": min(dates) if dates else None, "end": max(dates) if dates else None, "provenance": "metadata" if dates else "unavailable"}, "episode_inventory": episode_rows, "ready": bool(selection.selected_paths) and eligible > 0}
