from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SOURCE_CLASSIFICATIONS = {
    "new",
    "changed",
    "unchanged",
    "removed-from-source",
    "newly-eligible-by-speaker-selection",
    "invalid",
}


@dataclass
class ReconciliationPlan:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    metadata_only: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    source_classification: dict[str, str] = field(default_factory=dict)
    deletion_reasons: dict[str, str] = field(default_factory=dict)

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
            "source_classification": self.source_classification,
            "deletion_reasons": self.deletion_reasons,
        }


def source_identity(
    *,
    episode_id: str,
    source_fingerprint: str,
    schema_version: str,
    representation_id: str,
    content_hash: str,
) -> str:
    """Build an episode identity without relying on its display name or path."""
    import hashlib
    import json

    payload = {
        "episode_id": str(episode_id),
        "source_fingerprint": str(source_fingerprint),
        "schema_version": str(schema_version),
        "representation_id": str(representation_id),
        "content_hash": str(content_hash),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def classify_source_records(
    current: dict[str, dict[str, Any]],
    existing: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Classify source episodes/records using immutable identity fields."""
    result: dict[str, str] = {}
    for key, record in current.items():
        required = ("episode_id", "source_fingerprint", "schema_version", "representation_id", "content_hash")
        if not key or any(not str(record.get(field) or "") for field in required):
            result[key] = "invalid"
            continue
        before = existing.get(key)
        if before is None:
            result[key] = "new"
        elif record.get("eligible_speakers") and set(record.get("eligible_speakers") or set()).difference(before.get("eligible_speakers") or set()):
            result[key] = "newly-eligible-by-speaker-selection"
        elif record.get("source_identity") == before.get("source_identity"):
            result[key] = "unchanged"
        else:
            result[key] = "changed"
    for key in set(existing).difference(current):
        result[key] = "removed-from-source"
    return result


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
    plan.deletion_reasons = {item_id: "removed-from-source" for item_id in plan.removed}
    plan.source_classification = {
        item_id: ("new" if item_id in plan.added else "changed" if item_id in plan.changed else "unchanged")
        for item_id in current
    }
    plan.source_classification.update(plan.deletion_reasons)
    return plan


def require_delete_confirmation(plan: ReconciliationPlan, allow_delete_missing: bool, *, reconcile: bool | None = None) -> None:
    # ``reconcile=None`` preserves the low-level helper's historical contract;
    # callers that expose a user workflow pass an explicit boolean.
    permitted = allow_delete_missing if reconcile is None else (allow_delete_missing and reconcile)
    if plan.removed and not permitted:
        preview = ", ".join(plan.removed[:5])
        suffix = "" if len(plan.removed) <= 5 else f" and {len(plan.removed) - 5} more"
        raise PermissionError(
            f"Update would remove {len(plan.removed)} indexed document(s): {preview}{suffix}. "
            "Review the dry-run and rerun explicit reconciliation with --reconcile --allow-delete-missing."
        )
