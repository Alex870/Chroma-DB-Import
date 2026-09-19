"""Scoped, reviewable settings transfer contracts for the Modern UI.

Transfers are intentionally small allowlisted documents.  They never contain
runtime configuration, database content, active-release pointers, jobs, or
secrets, and importing one cannot address an arbitrary field path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import BridgeError, new_id, stable_hash


SCHEMA_VERSION = "gui-settings-transfer-v1"
PROPOSAL_SCHEMA_VERSION = "gui-settings-proposal-v1"
MAX_TRANSFER_BYTES = 1_000_000
ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "app_defaults": frozenset({"output_parent", "asset_filter", "asset_pattern", "execution_options"}),
    "database": frozenset({"selection_policy", "execution_options"}),
    "context": frozenset({"profile", "execution_options", "redundancy_policy"}),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BridgeError("VALIDATION_FAILED", f"{label} must be an object.", field=label)
    return dict(value)


def _scope_kind(value: Any) -> str:
    kind = str(value or "").strip()
    if kind not in ALLOWED_FIELDS:
        raise BridgeError("VALIDATION_FAILED", "scope_kind is not supported.", field="scope_kind")
    return kind


def _validate_fields(scope_kind: str, settings: Mapping[str, Any]) -> None:
    unknown = sorted(set(settings).difference(ALLOWED_FIELDS[scope_kind]))
    if unknown:
        raise BridgeError("VALIDATION_FAILED", f"Unsupported settings field: {unknown[0]}.", field=unknown[0])


def build_transfer(scope_kind: str, identity: Mapping[str, Any], settings: Mapping[str, Any], *, excluded_fields: Sequence[str] = ()) -> dict[str, Any]:
    kind = _scope_kind(scope_kind)
    values = _mapping(settings, "settings")
    _validate_fields(kind, values)
    excluded = sorted({str(item) for item in excluded_fields if str(item)})
    return {
        "schema_version": SCHEMA_VERSION,
        "scope_kind": kind,
        "exported_at": _now(),
        "identity": copy.deepcopy(_mapping(identity, "identity")),
        "settings": copy.deepcopy(values),
        "included_fields": sorted(values),
        "excluded_fields": excluded,
    }


def validate_transfer(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = _mapping(value, "transfer")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise BridgeError("VALIDATION_FAILED", "The settings transfer schema is unsupported.", field="schema_version")
    kind = _scope_kind(payload.get("scope_kind"))
    identity = _mapping(payload.get("identity") or {}, "identity")
    settings = _mapping(payload.get("settings") or {}, "settings")
    _validate_fields(kind, settings)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope_kind": kind,
        "exported_at": str(payload.get("exported_at") or ""),
        "identity": identity,
        "settings": copy.deepcopy(settings),
        "included_fields": sorted(settings),
        "excluded_fields": sorted({str(item) for item in payload.get("excluded_fields") or [] if str(item)}),
    }


def normalize_legacy_transfer(value: Mapping[str, Any], scope_kind: str, identity: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Convert the small, known legacy UI state surface into a reviewable transfer.

    Legacy state is intentionally treated as a source of optional values, not as
    an opaque profile replacement.  Fields that cannot be mapped to verified
    modern identities are excluded and remain visible in the transfer metadata.
    """
    raw = _mapping(value, "legacy settings")
    kind = _scope_kind(scope_kind)
    settings: dict[str, Any] = {}
    excluded = ["runtime_config", "secrets", "jobs", "active_release_pointer", "database_content"]

    if kind == "app_defaults":
        for name in ("output_parent", "asset_filter", "asset_pattern"):
            if name in raw:
                settings[name] = copy.deepcopy(raw[name])
        if "output_parent" not in settings and "output_root" in raw:
            settings["output_parent"] = copy.deepcopy(raw["output_root"])
        if "embedding_device" in raw:
            settings["execution_options"] = {"embedding_device": raw["embedding_device"]}
    elif kind == "database":
        if isinstance(raw.get("selection_policy"), Mapping):
            settings["selection_policy"] = copy.deepcopy(raw["selection_policy"])
        else:
            selection: dict[str, Any] = {}
            for name in ("speaker_mode", "allowlist_speakers", "excluded_speakers", "episode_overrides", "excluded_episode_ids", "asset_filter", "asset_pattern"):
                if name in raw:
                    selection[name] = copy.deepcopy(raw[name])
            if "included_speakers_by_episode" in raw:
                selection["episode_overrides"] = copy.deepcopy(raw["included_speakers_by_episode"])
            if selection:
                settings["selection_policy"] = selection
        if isinstance(raw.get("execution_options"), Mapping):
            settings["execution_options"] = copy.deepcopy(raw["execution_options"])
        elif "embedding_device" in raw:
            settings["execution_options"] = {"embedding_device": raw["embedding_device"]}
    else:
        profile = copy.deepcopy(raw.get("profile")) if isinstance(raw.get("profile"), Mapping) else {}
        profile_fields = ("selection_policy", "representation_profile", "embedding_model", "embedding_model_revision", "asset_filter", "asset_pattern", "included_speakers_by_episode")
        for name in profile_fields:
            if name in raw and name not in profile:
                profile[name] = copy.deepcopy(raw[name])
        if profile:
            if "included_speakers_by_episode" in profile and "selection_policy" not in profile:
                profile["selection_policy"] = {"episode_overrides": profile.pop("included_speakers_by_episode")}
            settings["profile"] = profile
        if isinstance(raw.get("execution_options"), Mapping):
            settings["execution_options"] = copy.deepcopy(raw["execution_options"])
        elif "embedding_device" in raw:
            settings["execution_options"] = {"embedding_device": raw["embedding_device"]}
        if isinstance(raw.get("redundancy_policy"), Mapping):
            settings["redundancy_policy"] = copy.deepcopy(raw["redundancy_policy"])

    for name in ("speaker_fingerprints", "episode_speaker_fingerprints", "included_speaker_fingerprints"):
        if name in raw:
            excluded.append(f"{name}: no unique verified episode mapping")
    if not settings:
        raise BridgeError("VALIDATION_FAILED", "The legacy settings file contains no compatible fields.", field="path")
    source_identity = dict(identity or {})
    for name in ("database_id", "connection_id", "partition_id", "corpus_id"):
        if name in raw:
            source_identity[name] = raw[name]
    transfer = build_transfer(kind, source_identity, settings, excluded_fields=excluded)
    transfer["source_format"] = "legacy-ui-state"
    return transfer


def write_transfer(path: str | Path, transfer: Mapping[str, Any]) -> dict[str, Any]:
    payload = validate_transfer(transfer)
    destination = Path(path).expanduser().resolve()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except OSError as exc:
        raise BridgeError("JOB_FAILED", f"The settings export could not be written: {exc}") from exc
    return {"path": str(destination), "scope_kind": payload["scope_kind"], "included_fields": payload["included_fields"]}


def read_transfer(path: str | Path, *, scope_kind: str | None = None, identity: Mapping[str, Any] | None = None, max_bytes: int = MAX_TRANSFER_BYTES) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        size = source.stat().st_size
        if size > max_bytes:
            raise BridgeError("VALIDATION_FAILED", "The settings transfer is too large to review.", field="path")
        with source.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except BridgeError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError("VALIDATION_FAILED", "The settings transfer could not be read as JSON.", field="path") from exc
    if isinstance(value, Mapping) and value.get("schema_version") == SCHEMA_VERSION:
        return validate_transfer(value)
    if scope_kind is None:
        raise BridgeError("VALIDATION_FAILED", "The settings transfer schema is unsupported.", field="schema_version")
    return normalize_legacy_transfer(value, scope_kind, identity)


def _identity_issues(transfer: Mapping[str, Any], target_scope: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    if str(transfer.get("scope_kind") or "") != str(target_scope.get("scope_kind") or ""):
        issues.append("The transfer scope does not match the selected target.")
    source_identity = dict(transfer.get("identity") or {})
    target_identity = dict(target_scope.get("identity") or {})
    for key in ("database_id", "connection_id", "partition_id", "corpus_id"):
        if source_identity.get(key) not in (None, "") and target_identity.get(key) not in (None, "") and str(source_identity[key]) != str(target_identity[key]):
            issues.append(f"Pinned identity {key} does not match the selected target.")
    return issues


def preview_import(transfer: Mapping[str, Any], target_scope: Mapping[str, Any], current: Mapping[str, Any], *, base_revision: int = 0) -> dict[str, Any]:
    payload = validate_transfer(transfer)
    target = _mapping(target_scope, "target_scope")
    current_values = _mapping(current, "current")
    issues = _identity_issues(payload, target)
    allowed = sorted(set(payload["settings"]).intersection(ALLOWED_FIELDS[str(payload["scope_kind"])]))
    changes = {
        field: {
            "field": field,
            "old": copy.deepcopy(current_values.get(field)),
            "new": copy.deepcopy(payload["settings"][field]),
            "status": "blocked" if issues else "allowed",
            "reason": "; ".join(issues) if issues else "Selected explicitly from the scoped transfer.",
        }
        for field in allowed
    }
    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "review_id": new_id("settings_review"),
        "scope_kind": payload["scope_kind"],
        "input_hash": stable_hash(payload),
        "target_identity": copy.deepcopy(dict(target.get("identity") or {})),
        "target_revision": int(target.get("revision") or base_revision),
        "target_hash": stable_hash(current_values),
        "allowed_field_paths": allowed if not issues else [],
        "changes": changes,
        "included_fields": allowed,
        "excluded_fields": payload["excluded_fields"],
        "compatibility": "blocked" if issues else "compatible",
        "reasons": issues,
    }


def apply_import(proposal: Mapping[str, Any], current: Mapping[str, Any], selected_fields: Sequence[str], *, current_revision: int = 0, transfer: Mapping[str, Any] | None = None) -> dict[str, Any]:
    review = _mapping(proposal, "proposal")
    if review.get("schema_version") != PROPOSAL_SCHEMA_VERSION:
        raise BridgeError("VALIDATION_FAILED", "The settings proposal schema is unsupported.")
    current_values = _mapping(current, "current")
    if stable_hash(current_values) != str(review.get("target_hash") or "") or int(review.get("target_revision") or 0) != int(current_revision):
        raise BridgeError("STALE_SETTINGS", "Settings changed after the proposal was prepared. Review the transfer again.")
    selected = sorted({str(field) for field in selected_fields if str(field)})
    allowed = set(review.get("allowed_field_paths") or [])
    if set(selected).difference(allowed):
        raise BridgeError("VALIDATION_FAILED", "A selected settings field is not in the reviewed proposal.")
    if review.get("compatibility") != "compatible":
        raise BridgeError("VALIDATION_FAILED", "The settings proposal is not compatible with the selected target.")
    if transfer is not None and stable_hash(validate_transfer(transfer)) != str(review.get("input_hash") or ""):
        raise BridgeError("STALE_SETTINGS", "The transfer file changed after the proposal was prepared. Review it again.")
    result = copy.deepcopy(current_values)
    changes = dict(review.get("changes") or {})
    for field in selected:
        result[field] = copy.deepcopy(changes[field]["new"])
    return {"value": result, "applied_fields": selected, "skipped_fields": sorted(set(allowed).difference(selected)), "revision": int(current_revision) + (1 if selected else 0)}


__all__ = ["ALLOWED_FIELDS", "MAX_TRANSFER_BYTES", "SCHEMA_VERSION", "apply_import", "build_transfer", "preview_import", "read_transfer", "validate_transfer", "write_transfer"]
