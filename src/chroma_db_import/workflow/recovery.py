from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import AppCatalog


def inspect_activation(catalog: AppCatalog, target: Path, stage: Path, backup: Path | None = None) -> dict[str, Any]:
    target_present = target.exists()
    stage_present = stage.exists()
    backup_present = bool(backup and backup.exists())
    if target_present and backup_present:
        active_state = "unknown"
        transition = "ambiguous_multiple_copies"
    elif target_present:
        active_state = "new_version_active"
        transition = "new_target_present"
    elif backup_present and stage_present:
        active_state = "unchanged"
        transition = "target_moved_to_backup"
    elif backup_present:
        active_state = "unchanged"
        transition = "old_target_present_only_as_backup"
    elif stage_present:
        active_state = "unavailable"
        transition = "staged_target_only"
    else:
        active_state = "unknown"
        transition = "no_known_copy"
    return {
        "target_present": target_present,
        "stage_present": stage_present,
        "backup_present": backup_present,
        "transition": transition,
        "active_database_state": active_state,
    }


def recover_interrupted(catalog: AppCatalog) -> list[dict[str, Any]]:
    """Conservatively report journal evidence; never delete an uncertain copy."""
    results = []
    for entry in catalog.list_activation_journals():
        target = Path(entry["target_path"])
        stage = Path(entry["stage_path"])
        backup = Path(entry["backup_path"]) if entry.get("backup_path") else None
        results.append({**entry, "evidence": inspect_activation(catalog, target, stage, backup)})
    return results
