"""Portable deduplication ledger and release validation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from .deduplication import (
    DEDUP_ARTIFACT_CONTRACT,
    DEDUP_CAPABILITY,
    DedupDecision,
    DedupInput,
    DedupPlan,
    DeduplicationError,
    normalize_text_v1,
    resolve_dedup_policy,
    sha256_digest,
)
from .contract import temporal_coverage_stats, temporal_record_eligibility


class DedupArtifactError(DeduplicationError):
    """Raised when a portable dedup artifact is not safe to consume."""


_LOCAL_KEY_RE = re.compile(r"(path|root|directory|dir|secret|token|password|credential|cache)", re.I)
_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")


def _portable(value: Any, *, key: str = "") -> Any:
    if _LOCAL_KEY_RE.search(key):
        return "<local>" if value not in (None, "") else value
    if isinstance(value, Mapping):
        return {str(name): _portable(item, key=str(name)) for name, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, list):
        return [_portable(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_portable(item, key=key) for item in value]
    if isinstance(value, str) and _ABSOLUTE_RE.match(value):
        return "<local>"
    if isinstance(value, float) and not math.isfinite(value):
        raise DedupArtifactError("portable metadata contains a non-finite number")
    return value


def portable_occurrence(decision: DedupDecision, item: DedupInput | None = None) -> dict[str, Any]:
    """Render one occurrence without private cache paths or runtime state."""
    item = item or DedupInput(
        effective_id=decision.effective_id,
        node_id="",
        page_content="",
        producer_metadata={},
        partition_id="",
        corpus_id="",
        episode_uid="",
        verified_cache_fingerprint="",
    )
    return {
        "document_id": item.effective_id,
        "node_id": item.node_id,
        "partition_id": item.partition_id,
        "corpus_id": item.corpus_id,
        "episode_uid": item.episode_uid,
        "verified_cache_fingerprint": item.verified_cache_fingerprint,
        "page_content": item.page_content,
        "producer_metadata": _portable(dict(item.producer_metadata)),
        "normalized_text_hash": item.normalized_text_hash,
        "producer_metadata_hash": item.producer_metadata_hash,
        "embedding_input_hash": item.embedding_input_hash,
        "source_span_hash": item.source_span_hash,
        "duplicate_group_id": decision.duplicate_group_id,
        "preferred_canonical_id": decision.preferred_canonical_id or item.effective_id,
        "storage_status": decision.storage_status,
        "alias_target_id": decision.alias_target_id,
        "decision_method": decision.method,
        "decision_reason": decision.reason,
    }


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def _json_bytes(value: Any, *, indent: int | None = None) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":") if indent is None else None, indent=indent, allow_nan=False) + "\n").encode("utf-8")


def _hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _identity(release_identity: Mapping[str, Any]) -> dict[str, Any]:
    nested = release_identity.get("partition") if isinstance(release_identity.get("partition"), Mapping) else release_identity
    get = lambda key, default="": release_identity.get(key, nested.get(key, default))
    required = {
        "release_id": str(release_identity.get("release_id") or ""),
        "upstream_release_id": str(release_identity.get("upstream_release_id") or ""),
        "partition_id": str(get("partition_id") or ""),
        "corpus_id": str(get("corpus_id") or ""),
        "representation_id": str(release_identity.get("representation_id") or ""),
    }
    if any(not value for value in required.values()):
        raise DedupArtifactError("release identity is incomplete for dedup artifacts")
    for key in ("partition_display_name", "context_type", "workflow_profile", "partition_config_fingerprint"):
        if get(key) not in (None, ""):
            required[key] = get(key)
    if isinstance(release_identity.get("handoff_ids"), list):
        required["handoff_ids"] = sorted({str(value) for value in release_identity["handoff_ids"] if str(value)})
    return required


def write_dedup_artifacts(
    export_root: Path,
    plan: DedupPlan,
    release_identity: Mapping[str, Any],
    runtime_counts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write deterministic ledger/manifest files and attach references.

    The ledger is hashed before the manifest is written.  The manifest is then
    hashed and referenced from release/import manifests, avoiding a cyclic hash.
    """
    export_root = Path(export_root).resolve()
    export_root.mkdir(parents=True, exist_ok=True)
    identity = _identity(release_identity)
    policy = resolve_dedup_policy(plan.policy, default_profile="off")
    if policy["profile"] == "off":
        return {"enabled": False, "artifacts": []}
    rows = [portable_occurrence(plan.decisions[item_id], plan.inputs[item_id]) for item_id in sorted(plan.inputs)]
    temporal_coverage = temporal_coverage_stats([row.get("producer_metadata") or {} for row in rows])
    ledger_bytes = b"".join(_json_bytes(row) for row in rows)
    ledger_path = export_root / "dedup_occurrences.jsonl"
    _write_atomic(ledger_path, ledger_bytes)
    ledger_hash = _hash_bytes(ledger_bytes)
    counts = plan.as_counts()
    near = dict(plan.near_report)
    manifest = {
        "contract_version": DEDUP_ARTIFACT_CONTRACT,
        "release_contract_version": "chroma-export-release-v2",
        **identity,
        "policy": policy,
        "policy_fingerprint": sha256_digest(policy),
        "plan_fingerprint": plan.plan_fingerprint,
        "counts": counts,
        "coverage": {
            "input": plan.input_count,
            "excluded": plan.excluded_count,
            "eligible": plan.eligible_count,
            "stored": plan.stored_count,
            "suppressed_exact": plan.suppressed_exact_count,
            "would_suppress_exact": plan.would_suppress_exact,
        },
        "exact_groups": [group.as_dict() for group in plan.exact_groups],
        "near_edges": [edge.as_dict() for edge in plan.near_edges],
        "near": near,
        "occurrence_ledger": {"path": "dedup_occurrences.jsonl", "sha256": ledger_hash, "row_count": len(rows)},
        "runtime": _portable(dict(runtime_counts or {})),
        "required_capabilities": [DEDUP_CAPABILITY],
        "temporal_capability": temporal_coverage.get("temporal_capability", "legacy"),
        "temporal_coverage": temporal_coverage,
    }
    manifest_bytes = _json_bytes(manifest, indent=2)
    manifest_path = export_root / "dedup_manifest.json"
    _write_atomic(manifest_path, manifest_bytes)
    manifest_hash = _hash_bytes(manifest_bytes)
    reference = {
        "contract_version": DEDUP_ARTIFACT_CONTRACT,
        "manifest_path": "dedup_manifest.json",
        "manifest_sha256": manifest_hash,
        "ledger_path": "dedup_occurrences.jsonl",
        "ledger_sha256": ledger_hash,
        "policy_fingerprint": manifest["policy_fingerprint"],
        "plan_fingerprint": manifest["plan_fingerprint"],
    }
    for filename in ("release.json", "import_manifest.json"):
        path = export_root / filename
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DedupArtifactError(f"cannot attach dedup reference to {filename}: {exc}") from exc
        payload["dedup"] = reference
        payload["required_capabilities"] = sorted(set(payload.get("required_capabilities") or []) | {DEDUP_CAPABILITY})
        if filename == "release.json":
            payload["release_contract_version"] = "chroma-export-release-v2"
        _write_atomic(path, _json_bytes(payload, indent=2))
    return {"enabled": True, "manifest": str(manifest_path), "ledger": str(ledger_path), "manifest_sha256": manifest_hash, "ledger_sha256": ledger_hash, "plan_fingerprint": plan.plan_fingerprint}


def _safe_relative(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or "\\" in relative:
        raise DedupArtifactError("dedup artifact path is not a safe relative path")
    raw_candidate = root / relative
    if raw_candidate.is_symlink():
        raise DedupArtifactError("dedup artifact path may not be a symlink")
    candidate = raw_candidate.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise DedupArtifactError("dedup artifact path escapes export") from exc
    return candidate


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))


def _validate_portable_value(value: Any, *, key: str = "") -> None:
    """Reject local paths or unmasked secret-like metadata in a ledger."""
    if _LOCAL_KEY_RE.search(key) and value not in (None, "", "<local>"):
        raise DedupArtifactError("dedup ledger contains unmasked local or secret metadata")
    if isinstance(value, str) and _ABSOLUTE_RE.match(value):
        raise DedupArtifactError("dedup ledger contains an absolute local path")
    if isinstance(value, Mapping):
        for name, child in value.items():
            _validate_portable_value(child, key=str(name))
    elif isinstance(value, list):
        for child in value:
            _validate_portable_value(child, key=key)
    elif isinstance(value, float) and not math.isfinite(value):
        raise DedupArtifactError("dedup ledger contains a non-finite metadata value")


def _load_ledger(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DedupArtifactError("dedup occurrence ledger contains invalid JSON") from exc
        if not isinstance(value, dict):
            raise DedupArtifactError("dedup occurrence ledger rows must be objects")
        rows.append(value)
    ids = [row.get("document_id") for row in rows]
    if any(not isinstance(item_id, str) or not item_id for item_id in ids):
        raise DedupArtifactError("dedup occurrence ledger document IDs must be non-empty strings")
    if ids != sorted(ids):
        raise DedupArtifactError("dedup occurrence ledger IDs are not sorted")
    if len(set(ids)) != len(rows):
        raise DedupArtifactError("dedup occurrence ledger contains duplicate IDs")
    return rows


def _recomputed_plan_fingerprint(manifest: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> str:
    decisions = [
        {
            "id": str(row.get("document_id") or ""),
            "status": row.get("storage_status"),
            "group": row.get("duplicate_group_id"),
            "canonical": row.get("preferred_canonical_id"),
            "alias": row.get("alias_target_id"),
            "method": row.get("decision_method"),
            "reason": row.get("decision_reason"),
            "partition_id": row.get("partition_id"),
            "corpus_id": row.get("corpus_id"),
            "episode_uid": row.get("episode_uid"),
            "verified_cache_fingerprint": row.get("verified_cache_fingerprint"),
            "normalized_text_hash": row.get("normalized_text_hash"),
            "source_span_hash": row.get("source_span_hash"),
            "producer_metadata_hash": row.get("producer_metadata_hash"),
            "embedding_input_hash": row.get("embedding_input_hash"),
        }
        for row in rows
    ]
    near_report = dict(manifest.get("near") or {})
    near_report.pop("generated_at", None)
    payload = {
        "policy": manifest.get("policy"),
        "decisions": decisions,
        "groups": sorted(list(manifest.get("exact_groups") or []), key=lambda value: value.get("duplicate_group_id", "")),
        "near_edges": sorted(list(manifest.get("near_edges") or []), key=lambda value: (value.get("left_id", ""), value.get("right_id", ""))),
        "coverage": {
            "input": (manifest.get("coverage") or {}).get("input"),
            "excluded": (manifest.get("coverage") or {}).get("excluded"),
            "eligible": (manifest.get("coverage") or {}).get("eligible"),
            "stored": (manifest.get("coverage") or {}).get("stored"),
            "suppressed_exact": (manifest.get("coverage") or {}).get("suppressed_exact"),
            "would_suppress_exact": (manifest.get("coverage") or {}).get("would_suppress_exact"),
        },
        "near_report": {key: near_report[key] for key in sorted(near_report)},
    }
    return sha256_digest(payload)


def validate_dedup_artifacts(
    export_root: Path,
    release: Mapping[str, Any],
    *,
    stored_records: Iterable[str] | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate all portable references and return an inventory summary."""
    export_root = Path(export_root).resolve()
    reference = release.get("dedup") if isinstance(release.get("dedup"), Mapping) else None
    if not reference or reference.get("contract_version") != DEDUP_ARTIFACT_CONTRACT:
        raise DedupArtifactError("release has no dedup reference")
    manifest_path = _safe_relative(export_root, reference.get("manifest_path"))
    ledger_path = _safe_relative(export_root, reference.get("ledger_path"))
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise DedupArtifactError("dedup manifest is unavailable") from exc
    if _hash_bytes(manifest_bytes) != reference.get("manifest_sha256"):
        raise DedupArtifactError("dedup manifest hash mismatch")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DedupArtifactError("dedup manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, Mapping):
        raise DedupArtifactError("dedup manifest must be an object")
    if manifest.get("contract_version") != DEDUP_ARTIFACT_CONTRACT or manifest.get("release_contract_version") != "chroma-export-release-v2" or DEDUP_CAPABILITY not in (manifest.get("required_capabilities") or []):
        raise DedupArtifactError("unsupported dedup artifact contract")
    if release.get("release_contract_version") != "chroma-export-release-v2" or DEDUP_CAPABILITY not in (release.get("required_capabilities") or []):
        raise DedupArtifactError("dedup artifacts require downstream v2 capability declaration")
    try:
        ledger_bytes = ledger_path.read_bytes()
    except OSError as exc:
        raise DedupArtifactError("dedup occurrence ledger is unavailable") from exc
    if _hash_bytes(ledger_bytes) != reference.get("ledger_sha256") or _hash_bytes(ledger_bytes) != manifest.get("occurrence_ledger", {}).get("sha256"):
        raise DedupArtifactError("dedup ledger hash mismatch")
    rows = _load_ledger(ledger_path)
    policy = resolve_dedup_policy(manifest.get("policy"), default_profile="off")
    if sha256_digest(policy) != manifest.get("policy_fingerprint") or manifest.get("plan_fingerprint") != reference.get("plan_fingerprint"):
        raise DedupArtifactError("dedup policy or plan fingerprint mismatch")
    for key in ("partition_id", "corpus_id", "release_id", "upstream_release_id", "representation_id"):
        if str(manifest.get(key) or "") != str(release.get(key) or ""):
            raise DedupArtifactError(f"dedup manifest {key} mismatch")
    required_row_fields = {
        "document_id", "node_id", "partition_id", "corpus_id", "episode_uid",
        "verified_cache_fingerprint", "page_content", "producer_metadata",
        "normalized_text_hash", "producer_metadata_hash", "embedding_input_hash",
        "source_span_hash", "duplicate_group_id", "preferred_canonical_id",
        "storage_status", "alias_target_id", "decision_method", "decision_reason",
    }
    for row in rows:
        if set(row) != required_row_fields:
            raise DedupArtifactError("dedup occurrence ledger has an unexpected schema")
        for field_name in (
            "document_id", "node_id", "partition_id", "corpus_id", "episode_uid",
            "verified_cache_fingerprint", "normalized_text_hash", "producer_metadata_hash",
            "embedding_input_hash", "preferred_canonical_id", "storage_status",
            "decision_method", "decision_reason",
        ):
            if not isinstance(row.get(field_name), str) or not row[field_name]:
                raise DedupArtifactError(f"dedup occurrence {field_name} must be a non-empty string")
        if not _is_sha256(row["normalized_text_hash"]) or not _is_sha256(row["producer_metadata_hash"]) or not _is_sha256(row["embedding_input_hash"]):
            raise DedupArtifactError("dedup occurrence hashes must be sha256 values")
        if row["source_span_hash"] is not None and not _is_sha256(row["source_span_hash"]):
            raise DedupArtifactError("dedup occurrence source_span_hash must be null or sha256")
        for optional_field in ("duplicate_group_id", "alias_target_id"):
            if row[optional_field] is not None and (not isinstance(row[optional_field], str) or not row[optional_field]):
                raise DedupArtifactError(f"dedup occurrence {optional_field} must be null or a non-empty string")
        if not isinstance(row["page_content"], str) or not isinstance(row["producer_metadata"], Mapping):
            raise DedupArtifactError("dedup occurrence content or producer metadata has an invalid type")
        _validate_portable_value(row["producer_metadata"])
        if str(release.get("temporal_capability") or "legacy") == "certified" and not temporal_record_eligibility(dict(row["producer_metadata"]), episode_uid=str(row.get("episode_uid") or ""))["eligible"]:
            raise DedupArtifactError("certified temporal release contains an ineligible occurrence")
    row_ids = {row["document_id"] for row in rows}
    if any(not item for item in row_ids) or len(rows) != manifest.get("occurrence_ledger", {}).get("row_count"):
        raise DedupArtifactError("dedup ledger row inventory mismatch")
    exact_groups = manifest.get("exact_groups")
    near_edges = manifest.get("near_edges")
    if not isinstance(exact_groups, list) or not isinstance(near_edges, list):
        raise DedupArtifactError("dedup groups and near edges must be arrays")
    group_map: dict[str, set[str]] = {}
    row_by_id = {str(row.get("document_id")): row for row in rows}
    for group in exact_groups:
        if not isinstance(group, Mapping):
            raise DedupArtifactError("dedup exact group must be an object")
        members = [str(item) for item in group.get("member_ids") or []]
        canonical = str(group.get("canonical_id") or "")
        group_id = str(group.get("duplicate_group_id") or "")
        if not group_id or group_id in group_map or not canonical or len(members) < 2 or len(set(members)) != len(members) or any(not isinstance(item, str) or not item for item in members) or members != sorted(members) or canonical != members[0] or not set(members) <= row_ids:
            raise DedupArtifactError("dedup exact group membership is invalid")
        group_map[group_id] = set(members)
        if row_by_id.get(canonical, {}).get("storage_status") != "retained" or any(row_by_id[item_id].get("duplicate_group_id") != group_id for item_id in members):
            raise DedupArtifactError("dedup group canonical is not retained")
    for row in rows:
        if row.get("partition_id") != manifest.get("partition_id") or row.get("corpus_id") != manifest.get("corpus_id") or not str(row.get("episode_uid") or "").startswith(str(manifest.get("partition_id")) + ":"):
            raise DedupArtifactError("dedup occurrence scope or cache binding is invalid")
        if row.get("normalized_text_hash") != sha256_digest(normalize_text_v1(row.get("page_content"))):
            raise DedupArtifactError("dedup occurrence normalized text hash mismatch")
        status = row.get("storage_status")
        if status not in {"retained", "suppressed_exact"}:
            raise DedupArtifactError("dedup occurrence has invalid storage status")
        group_id = row.get("duplicate_group_id")
        if group_id is not None and group_id not in group_map:
            raise DedupArtifactError("dedup occurrence references a foreign exact group")
        if group_id is not None and str(row.get("document_id")) not in group_map[group_id]:
            raise DedupArtifactError("dedup occurrence is outside its exact group")
        canonical = str(row.get("preferred_canonical_id") or "")
        if not canonical or (group_id is None and canonical != row.get("document_id")):
            raise DedupArtifactError("dedup occurrence is missing preferred_canonical_id")
        if status == "suppressed_exact":
            alias = str(row.get("alias_target_id") or "")
            if policy["profile"] != "safe" or not group_id or not alias or alias == row.get("document_id") or alias != canonical or alias not in row_ids or row_by_id[alias].get("alias_target_id") not in (None, ""):
                raise DedupArtifactError("suppressed occurrence has an invalid direct alias")
        elif row.get("alias_target_id") not in (None, ""):
            raise DedupArtifactError("retained occurrence may not have an alias target")
    edges = near_edges
    seen_edges: set[tuple[str, str]] = set()
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise DedupArtifactError("dedup near edge must be an object")
        left, right = str(edge.get("left_id") or ""), str(edge.get("right_id") or "")
        similarity, ratio = edge.get("similarity"), edge.get("length_ratio")
        left_row, right_row = row_by_id.get(left), row_by_id.get(right)
        if (left, right) in seen_edges or not left or not right or left >= right or left not in row_ids or right not in row_ids or not left_row or not right_row or left_row.get("storage_status") != "retained" or right_row.get("storage_status") != "retained" or (left_row.get("producer_metadata") or {}).get("node_type") != edge.get("node_type") or (right_row.get("producer_metadata") or {}).get("node_type") != edge.get("node_type") or edge.get("partition_id") != manifest.get("partition_id") or edge.get("corpus_id") != manifest.get("corpus_id") or not isinstance(similarity, (int, float)) or isinstance(similarity, bool) or not math.isfinite(float(similarity)) or not 0 <= similarity <= 1 or not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not math.isfinite(float(ratio)) or not 0 <= ratio <= 1:
            raise DedupArtifactError("dedup near edge is invalid")
        seen_edges.add((left, right))
    expected_coverage = manifest.get("coverage") or {}
    counts = manifest.get("counts") or {}
    if any(counts.get(name) != expected_coverage.get(name) for name in ("input", "excluded", "eligible", "stored", "suppressed_exact", "would_suppress_exact")):
        raise DedupArtifactError("dedup counts and coverage disagree")
    actual_stored = sum(row.get("storage_status") == "retained" for row in rows)
    actual_suppressed = sum(row.get("storage_status") == "suppressed_exact" for row in rows)
    if expected_coverage.get("eligible") != len(rows) or expected_coverage.get("stored") != actual_stored or expected_coverage.get("suppressed_exact") != actual_suppressed or expected_coverage.get("eligible") != actual_stored + actual_suppressed:
        raise DedupArtifactError("dedup coverage equations are inconsistent")
    if _recomputed_plan_fingerprint(manifest, rows) != manifest.get("plan_fingerprint"):
        raise DedupArtifactError("dedup plan fingerprint does not match portable decisions")
    if stored_records is not None:
        stored_ids = set(str(item) for item in (stored_records.keys() if isinstance(stored_records, Mapping) else stored_records))
        expected = {str(row["document_id"]) for row in rows if row["storage_status"] == "retained"}
        if stored_ids != expected:
            raise DedupArtifactError("Chroma stored ID inventory disagrees with dedup ledger")
    for group_id, members in group_map.items():
        member_rows = [row_by_id[item_id] for item_id in members]
        if any(str(row.get("preferred_canonical_id") or "") != min(members) for row in member_rows):
            raise DedupArtifactError("exact group preferred canonical is inconsistent")
        if policy["profile"] == "safe":
            if sum(row.get("storage_status") == "retained" for row in member_rows) != 1 or any(row.get("storage_status") == "suppressed_exact" and not row.get("alias_target_id") for row in member_rows):
                raise DedupArtifactError("Safe exact group does not have one canonical and direct aliases")
        elif any(row.get("storage_status") != "retained" for row in member_rows):
            raise DedupArtifactError("Audit exact group contains a suppressed occurrence")
    return {
        "valid": True,
        "manifest": manifest,
        "rows": rows,
        "stored_ids": sorted(row["document_id"] for row in rows if row["storage_status"] == "retained"),
        "suppressed_ids": sorted(row["document_id"] for row in rows if row["storage_status"] == "suppressed_exact"),
        "exact_group_count": len(group_map),
        "near_edge_count": len(edges),
    }


__all__ = ["DedupArtifactError", "portable_occurrence", "validate_dedup_artifacts", "write_dedup_artifacts"]
