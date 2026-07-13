from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReconciliationPlan:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    metadata_only: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def requires_delete_confirmation(self) -> bool:
        return bool(self.removed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "changed": self.changed,
            "metadata_only": self.metadata_only,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "requires_delete_confirmation": self.requires_delete_confirmation,
        }


def plan_reconciliation(current: dict[str, dict[str, str]], existing: dict[str, dict[str, str]]) -> ReconciliationPlan:
    plan = ReconciliationPlan()
    for document_id in sorted(current):
        now = current[document_id]
        before = existing.get(document_id)
        if before is None:
            plan.added.append(document_id)
        elif now.get("embedding_fingerprint") != before.get("embedding_fingerprint"):
            plan.changed.append(document_id)
        elif now.get("metadata_fingerprint") != before.get("metadata_fingerprint"):
            plan.metadata_only.append(document_id)
        else:
            plan.unchanged.append(document_id)
    plan.removed = sorted(set(existing).difference(current))
    return plan


def require_delete_confirmation(plan: ReconciliationPlan, allow_delete_missing: bool) -> None:
    if plan.removed and not allow_delete_missing:
        preview = ", ".join(plan.removed[:5])
        suffix = "" if len(plan.removed) <= 5 else f" and {len(plan.removed) - 5} more"
        raise PermissionError(
            f"Update would remove {len(plan.removed)} indexed document(s): {preview}{suffix}. "
            "Review the dry-run and rerun with --allow-delete-missing."
        )
