"""Portable semantic-redundancy bundle writer, validator, and publisher."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .redundancy_evidence import build_occurrence_index_from_rows, build_representative_map, build_compact_vectors, write_evidence_inventory
from .redundancy_chroma import close_chroma_client
from .redundancy_inventory import Inventory, load_inventory
from .redundancy_models import CandidatePair, Scope
from .redundancy_policy import policy_fingerprint, resolve_redundancy_policy


BUNDLE_CONTRACT = "chroma-redundancy-bundle-v1"
BUNDLE_CAPABILITIES = {"occurrence-evidence-v1", "redundancy-assessment-v1", "shared-input-vectors-v1"}


class RedundancyArtifactError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _json_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _stored_vector_matches(actual: Iterable[Any], expected: Iterable[Any]) -> bool:
    """Accept exact JSON values or Chroma's documented float32 round-trip."""

    actual_values = [float(value) for value in actual]
    expected_values = [float(value) for value in expected]
    if len(actual_values) != len(expected_values):
        return False
    for actual_value, expected_value in zip(actual_values, expected_values):
        if actual_value == expected_value:
            continue
        try:
            stored_value = struct.unpack("f", struct.pack("f", expected_value))[0]
        except (OverflowError, struct.error):
            return False
        if actual_value != stored_value:
            return False
    return True


def _scope(value: Scope | Mapping[str, Any]) -> Scope:
    return value if isinstance(value, Scope) else Scope.from_mapping(value)


def _safe_relative(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or "\\" in value:
        raise RedundancyArtifactError("bundle artifact path is not a safe relative path")
    target = root / value
    if target.is_symlink():
        raise RedundancyArtifactError("bundle artifact paths may not be symlinks")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise RedundancyArtifactError("bundle artifact path escapes bundle root") from exc
    return target


def _rows(path: Path) -> list[dict[str, Any]]:
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise RedundancyArtifactError("artifact JSONL row is not an object")
            result.append(row)
    return result


def _pair_row(value: CandidatePair | Mapping[str, Any]) -> dict[str, Any]:
    return value.as_dict() if isinstance(value, CandidatePair) else dict(value)


def _identity_for_bundle(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": manifest.get("contract_version"),
        "scope": manifest.get("scope"),
        "base_hashes": manifest.get("base_hashes") or {},
        "base_fingerprint": manifest.get("base_fingerprint"),
        "policy_fingerprint": manifest.get("policy_fingerprint"),
        "policy": manifest.get("policy"),
        "input_spec_fingerprint": manifest.get("input_spec_fingerprint"),
        "artifacts": {key: value for key, value in sorted((manifest.get("artifacts") or {}).items()) if key not in {"bundle.json"}},
        "judgment_snapshot_hash": manifest.get("judgment_snapshot_hash"),
        "judgment_identity": {
            key: (manifest.get("judgment_status") or {}).get(key)
            for key in ("model", "model_artifact_id", "prompt_version", "schema_version")
        },
        "storage_mode": manifest.get("storage_mode"),
        "capabilities": sorted(manifest.get("capabilities") or []),
    }


def _runtime_excluded() -> dict[str, Any]:
    return {"created_at": time.time(), "timings": {}, "local_paths": {}}


def write_bundle(
    output_dir: str | Path,
    *,
    inventory: Inventory | Mapping[str, Any] | None = None,
    scope: Scope | Mapping[str, Any] | None = None,
    occurrences: Iterable[Mapping[str, Any]] | None = None,
    candidate_pairs: Iterable[CandidatePair | Mapping[str, Any]] = (),
    judgments: Iterable[Mapping[str, Any]] = (),
    policy: Mapping[str, Any] | None = None,
    coverage: Mapping[str, Any] | None = None,
    input_spec: Mapping[str, Any] | None = None,
    vectors: Mapping[str, Iterable[float]] | None = None,
    embedding_inputs: Mapping[str, str] | None = None,
    storage_mode: str | None = None,
    base_export: str | Path | None = None,
    status: str = "completed",
    judgment_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    policy_input = dict(policy or {})
    if storage_mode is not None:
        if "vector_storage" in policy_input and policy_input["vector_storage"] != storage_mode:
            raise RedundancyArtifactError("storage_mode conflicts with the resolved redundancy policy")
        policy_input["vector_storage"] = storage_mode
    resolved_policy = resolve_redundancy_policy(policy_input)
    if inventory is not None:
        inv = inventory if isinstance(inventory, Inventory) else None
        if inv:
            resolved_scope = inv.scope
            occurrence_rows = list(inv.occurrences)
            vector_map = dict(inv.vectors)
            base_hashes = dict(inv.hashes)
            default_base_fingerprint = _json_hash(base_hashes)
        else:
            payload = dict(inventory)
            resolved_scope = _scope(payload.get("scope") or scope or {})
            occurrence_rows = [dict(row) for row in payload.get("occurrences") or []]
            vector_map = dict(payload.get("vectors") or {})
            base_hashes = dict(payload.get("hashes") or {})
            default_base_fingerprint = _json_hash(base_hashes)
    else:
        if scope is None or occurrences is None:
            raise RedundancyArtifactError("scope and occurrences are required when inventory is absent")
        resolved_scope = _scope(scope)
        occurrence_rows = [dict(row) for row in occurrences]
        vector_map = dict(vectors or {})
        base_hashes = {}
        default_base_fingerprint = _json_hash({"scope": resolved_scope.as_dict(), "occurrences": occurrence_rows})
    if base_export is not None:
        try:
            base_inventory = load_inventory(base_export, inspect_vectors=True)
        except Exception as exc:
            raise RedundancyArtifactError("base export cannot be validated for bundle creation") from exc
        if base_inventory.scope != resolved_scope:
            raise RedundancyArtifactError("bundle scope does not match the supplied base export")
        base_hashes = dict(base_inventory.hashes)
        default_base_fingerprint = _json_hash(base_hashes)
        if not vector_map:
            vector_map = dict(base_inventory.vectors)
    storage = resolved_policy["vector_storage"]
    if storage not in {"full", "shared_input"}:
        raise RedundancyArtifactError("storage_mode must be full or shared_input")
    if status not in {"completed", "completed_partial", "disabled", "zero_budget", "failed", "cancelled"}:
        raise RedundancyArtifactError("unknown bundle assessment status")
    representation_payload = {}
    if inventory is not None:
        source_release = inv.release if inv is not None else dict(payload.get("release") or {})
        if isinstance(source_release, Mapping):
            representation_payload = dict(source_release.get("_analysis_representation") or source_release.get("representation") or {})
            if not representation_payload and isinstance(source_release.get("config"), Mapping):
                representation_payload = {"contextualization": source_release["config"].get("contextualization")}
    if isinstance(input_spec, Mapping) and input_spec.get("contextualization"):
        representation_payload["contextualization"] = input_spec.get("contextualization")
    representative_map = build_representative_map(occurrence_rows, vectors=vector_map, embedding_inputs=embedding_inputs, scope=resolved_scope, contextualization=str(representation_payload.get("contextualization") or "") or None)
    for row in occurrence_rows:
        mapping = next(item for item in representative_map if item["document_id"] == str(row.get("document_id")))
        row["representative_id"] = mapping["representative_id"]
    artifacts: dict[str, dict[str, Any]] = {}
    evidence_info = write_evidence_inventory(occurrence_rows, root / "evidence.jsonl", scope=resolved_scope, base_fingerprint=default_base_fingerprint)
    artifacts["evidence.jsonl"] = {"path": "evidence.jsonl", "sha256": evidence_info["sha256"], "row_count": evidence_info["row_count"]}
    occurrence_info = build_occurrence_index_from_rows(occurrence_rows, root / "occurrences.sqlite3")
    artifacts["occurrences.sqlite3"] = {"path": "occurrences.sqlite3", "sha256": occurrence_info["sha256"], "row_count": occurrence_info["row_count"]}
    pairs = [_pair_row(pair) for pair in candidate_pairs]
    (root / "candidate_pairs.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in sorted(pairs, key=lambda item: str(item.get("candidate_id") or item.get("left_id") or ""))), encoding="utf-8")
    artifacts["candidate_pairs.jsonl"] = {"path": "candidate_pairs.jsonl", "sha256": _sha256(root / "candidate_pairs.jsonl"), "row_count": len(pairs)}
    judgment_rows = [dict(row) for row in judgments]
    judge_disabled = str((coverage or {}).get("judge_status") or "") in {"disabled", "zero_budget", "not_requested"}
    if not judgment_rows and resolved_policy["judge_enabled"] and resolved_policy["judge_max_calls"] > 0 and status == "completed" and not judge_disabled:
        raise RedundancyArtifactError("empty judgments require an explicit disabled, zero-budget, or partial status")
    (root / "judgments.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in judgment_rows), encoding="utf-8")
    artifacts["judgments.jsonl"] = {"path": "judgments.jsonl", "sha256": _sha256(root / "judgments.jsonl"), "row_count": len(judgment_rows)}
    (root / "representative_map.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in representative_map), encoding="utf-8")
    artifacts["representative_map.jsonl"] = {"path": "representative_map.jsonl", "sha256": _sha256(root / "representative_map.jsonl"), "row_count": len(representative_map)}
    vector_backend = None
    logical_vector_map = dict(vector_map)
    if storage == "shared_input":
        compact_info = build_compact_vectors(representative_map, vector_map, root / "vectors", scope=resolved_scope)
        artifacts["vectors/vectors.json"] = {"path": "vectors/vectors.json", "sha256": compact_info["sha256"], "row_count": compact_info["representative_count"]}
        chroma_manifest_path = root / "vectors" / "chroma_manifest.json"
        artifacts["vectors/chroma_manifest.json"] = {"path": "vectors/chroma_manifest.json", "sha256": compact_info["chroma_manifest_sha256"], "row_count": 1}
        vector_backend = compact_info["backend"]
        compact_ids = sorted({str(item.get("representative_id") or "") for item in representative_map if str(item.get("representative_id") or "")})
        logical_vector_map = {key: vector_map[key] for key in compact_ids if key in vector_map}
    elif vector_map:
        vector_path = root / "vectors.json"
        vector_path.write_text(json.dumps({"scope": resolved_scope.as_dict(), "vectors": {key: list(value) for key, value in sorted(vector_map.items())}}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        artifacts["vectors.json"] = {"path": "vectors.json", "sha256": _sha256(vector_path), "row_count": len(vector_map)}
    judgment_snapshot_hash = _json_hash(judgment_rows)
    manifest = {
        "contract_version": BUNDLE_CONTRACT,
        "capabilities": sorted({"occurrence-evidence-v1", "redundancy-assessment-v1"} | ({"shared-input-vectors-v1"} if storage == "shared_input" else set())),
        "scope": resolved_scope.as_dict(),
        "base_hashes": base_hashes,
        "base_fingerprint": default_base_fingerprint,
        "policy": resolved_policy,
        "policy_fingerprint": policy_fingerprint(resolved_policy),
        "input_spec": dict(input_spec or {}),
        "input_spec_fingerprint": _json_hash(dict(input_spec or {})),
        "coverage": dict(coverage or {"unit_count": len(occurrence_rows), "candidate_count": len(pairs), "judgment_count": len(judgment_rows)}),
        "judgment_status": {"status": status, "model": None, "prompt_version": "redundancy_judge_v1", **dict(judgment_status or {})},
        "storage_mode": storage,
        "vector_backend": vector_backend,
        "artifacts": artifacts,
        "judgment_snapshot_hash": judgment_snapshot_hash,
        "logical_vector_digest": _json_hash({key: list(value) for key, value in sorted(logical_vector_map.items())}) if logical_vector_map else None,
        "runtime": _runtime_excluded(),
    }
    manifest["bundle_id"] = "bundle_" + _json_hash(_identity_for_bundle(manifest))[7:]
    (root / "bundle.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def validate_bundle(bundle_root: str | Path, *, base_export: str | Path | None = None, expected_scope: Scope | Mapping[str, Any] | None = None) -> dict[str, Any]:
    root = Path(bundle_root).expanduser().resolve()
    if not root.is_dir():
        raise RedundancyArtifactError("bundle root is not a directory")
    try:
        manifest = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RedundancyArtifactError("bundle manifest is invalid") from exc
    if manifest.get("contract_version") != BUNDLE_CONTRACT:
        raise RedundancyArtifactError("unsupported redundancy bundle contract")
    capabilities = set(manifest.get("capabilities") or [])
    if not capabilities <= BUNDLE_CAPABILITIES or not {"occurrence-evidence-v1", "redundancy-assessment-v1"} <= capabilities:
        raise RedundancyArtifactError("bundle capabilities are unknown or incomplete")
    storage_mode = manifest.get("storage_mode")
    if storage_mode not in {"full", "shared_input"}:
        raise RedundancyArtifactError("bundle storage mode is invalid")
    if storage_mode == "full" and "shared-input-vectors-v1" in capabilities:
        raise RedundancyArtifactError("full bundle advertises shared-input vectors")
    if storage_mode == "shared_input" and "shared-input-vectors-v1" not in capabilities:
        raise RedundancyArtifactError("shared-input bundle lacks its vector capability")
    try:
        resolved_policy = resolve_redundancy_policy(manifest.get("policy") or {})
    except Exception as exc:
        raise RedundancyArtifactError("bundle policy is invalid") from exc
    if dict(manifest.get("policy") or {}) != resolved_policy or manifest.get("policy_fingerprint") != policy_fingerprint(resolved_policy):
        raise RedundancyArtifactError("bundle policy fingerprint does not match its policy")
    if manifest.get("input_spec_fingerprint") != _json_hash(dict(manifest.get("input_spec") or {})):
        raise RedundancyArtifactError("bundle input-spec fingerprint does not match its input specification")
    actual_scope = _scope(manifest.get("scope") or {})
    if expected_scope is not None and actual_scope != _scope(expected_scope):
        raise RedundancyArtifactError("bundle scope does not match the requested scope")
    base_hashes = manifest.get("base_hashes") or {}
    if base_hashes and manifest.get("base_fingerprint") != _json_hash(base_hashes):
        raise RedundancyArtifactError("bundle base fingerprint does not match its base hashes")
    artifact_references = manifest.get("artifacts") or {}
    if not isinstance(artifact_references, Mapping):
        raise RedundancyArtifactError("bundle artifact references must be an object")
    required_artifacts = {"evidence.jsonl", "occurrences.sqlite3", "candidate_pairs.jsonl", "judgments.jsonl", "representative_map.jsonl"}
    if storage_mode == "shared_input":
        required_artifacts |= {"vectors/vectors.json", "vectors/chroma_manifest.json"}
    if not required_artifacts <= set(artifact_references):
        raise RedundancyArtifactError("bundle is missing required artifact references")
    for key, reference in artifact_references.items():
        if not isinstance(reference, Mapping):
            raise RedundancyArtifactError("bundle artifact reference is invalid")
        path = _safe_relative(root, reference.get("path"))
        if not path.is_file() or _sha256(path) != reference.get("sha256"):
            raise RedundancyArtifactError(f"bundle artifact hash mismatch: {key}")
        if key.endswith(".jsonl") and len(_rows(path)) != reference.get("row_count"):
            raise RedundancyArtifactError(f"bundle artifact row count mismatch: {key}")
    evidence = _rows(root / "evidence.jsonl")
    if len({str(row.get("document_id") or "") for row in evidence}) != len(evidence) or any(not row.get("document_id") for row in evidence):
        raise RedundancyArtifactError("bundle evidence IDs are missing or duplicated")
    for row in evidence:
        row_scope = row.get("scope") if isinstance(row.get("scope"), Mapping) else {
            "partition_id": row.get("partition_id"),
            "corpus_id": row.get("corpus_id"),
            "representation_id": row.get("representation_id"),
        }
        if any(value is not None and str(value) and str(value) != str(actual_scope.as_dict().get(key)) for key, value in row_scope.items() if key in actual_scope.as_dict()):
            raise RedundancyArtifactError("bundle evidence contains a foreign scope")
    representative_rows = _rows(root / "representative_map.jsonl")
    if {str(row.get("document_id") or "") for row in representative_rows} != {str(row.get("document_id") or "") for row in evidence}:
        raise RedundancyArtifactError("representative map does not cover exactly the evidence ledger")
    evidence_by_id = {str(row.get("document_id")): row for row in evidence}
    map_by_id = {str(row.get("document_id")): row for row in representative_rows}
    if any(dict(row.get("scope") or {}) != actual_scope.as_dict() for row in representative_rows if row.get("scope")):
        raise RedundancyArtifactError("representative map contains a foreign scope")
    if any(str(row.get("representative_id") or "") not in evidence_by_id for row in representative_rows):
        # Compact bundles still map to occurrence IDs; representative IDs must
        # be a member of the evidence ledger, never an opaque external ID.
        raise RedundancyArtifactError("representative map references a foreign representative")
    try:
        connection = sqlite3.connect(root / "occurrences.sqlite3")
        try:
            indexed_rows = {
                str(row[0]): {
                    "episode_uid": str(row[1]),
                    "node_type": str(row[2]),
                    "episode_date": str(row[3]),
                    "metadata_json": str(row[4]),
                    "text": str(row[5]),
                    "representative_id": row[6],
                    "embedding_fingerprint": str(row[7] or ""),
                    "embedding_input_hash": row[8],
                }
                for row in connection.execute("SELECT document_id, episode_uid, node_type, episode_date, metadata_json, text, representative_id, embedding_fingerprint, embedding_input_hash FROM occurrences")
            }
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise RedundancyArtifactError("occurrence index is unreadable") from exc
    if set(indexed_rows) != set(evidence_by_id):
        raise RedundancyArtifactError("occurrence index does not agree with evidence")
    for document_id, evidence_row in evidence_by_id.items():
        metadata = dict(evidence_row.get("metadata") or {})
        expected = {
            "episode_uid": str(evidence_row.get("episode_uid") or metadata.get("episode_uid") or ""),
            "node_type": str(evidence_row.get("node_type") or metadata.get("node_type") or ""),
            "episode_date": str(metadata.get("episode_date") or ""),
            "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "text": str(evidence_row.get("text") if evidence_row.get("text") is not None else evidence_row.get("page_content") or ""),
            "representative_id": evidence_row.get("representative_id"),
            "embedding_fingerprint": str(evidence_row.get("embedding_fingerprint") or ""),
            "embedding_input_hash": evidence_row.get("embedding_input_hash"),
        }
        if indexed_rows[document_id] != expected:
            raise RedundancyArtifactError("occurrence index row does not agree with evidence")
    candidate_rows = _rows(root / "candidate_pairs.jsonl")
    candidate_by_id: dict[str, dict[str, Any]] = {}
    for row in candidate_rows:
        left_id, right_id = str(row.get("left_id") or ""), str(row.get("right_id") or "")
        candidate_id = f"{left_id}::{right_id}"
        if not left_id or not right_id or left_id >= right_id or row.get("candidate_id") != candidate_id or left_id not in evidence_by_id or right_id not in evidence_by_id:
            raise RedundancyArtifactError("candidate snapshot contains an invalid or foreign pair")
        if candidate_id in candidate_by_id or not isinstance(row.get("channels"), list) or not isinstance(row.get("scores"), Mapping) or not isinstance(row.get("channel_ranks"), Mapping) or not isinstance(row.get("guards"), list):
            raise RedundancyArtifactError("candidate snapshot row is malformed or duplicated")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in (row.get("scores") or {}).values()):
            raise RedundancyArtifactError("candidate snapshot contains a non-finite score")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (row.get("channel_ranks") or {}).values()):
            raise RedundancyArtifactError("candidate snapshot contains an invalid channel rank")
        candidate_by_id[candidate_id] = row
    judgment_rows = _rows(root / "judgments.jsonl")
    if manifest.get("judgment_snapshot_hash") != _json_hash(judgment_rows):
        raise RedundancyArtifactError("judgment snapshot hash does not match judgments")
    judgment_keys = {"candidate_id", "relation", "matched_ids", "evidence", "novel_quotes", "conflict_quotes", "attribution_changed", "time_changed", "qualification_changed", "distinct_occurrence", "reason", "status"}
    for row in judgment_rows:
        if set(row) != judgment_keys or str(row.get("candidate_id") or "") not in evidence_by_id or row.get("relation") not in {"equivalent", "partial_overlap", "novel", "contradiction", "uncertain"} or row.get("status") != "retain_evidence":
            raise RedundancyArtifactError("judgment row has an invalid schema or candidate")
        matched_ids = row.get("matched_ids")
        if not isinstance(matched_ids, list) or any(str(value) not in evidence_by_id for value in matched_ids):
            raise RedundancyArtifactError("judgment references a foreign matched occurrence")
        if any(f"{min(str(row['candidate_id']), str(matched_id))}::{max(str(row['candidate_id']), str(matched_id))}" not in candidate_by_id for matched_id in matched_ids):
            raise RedundancyArtifactError("judgment references a pair absent from the candidate snapshot")
        if any(not isinstance(row.get(key), bool) for key in ("attribution_changed", "time_changed", "qualification_changed", "distinct_occurrence")) or not isinstance(row.get("reason"), str) or len(row.get("reason")) > 500:
            raise RedundancyArtifactError("judgment change flags or reason are invalid")
        candidate_text = str(evidence_by_id[str(row["candidate_id"])].get("text") or evidence_by_id[str(row["candidate_id"])].get("page_content") or "")
        for key in ("novel_quotes", "conflict_quotes"):
            if not isinstance(row.get(key), list) or any(not isinstance(quote, str) or not quote or len(quote) > 1000 or quote not in candidate_text for quote in row[key]):
                raise RedundancyArtifactError("judgment contains a fabricated or invalid quote")
        if not isinstance(row.get("evidence"), list) or len(row["evidence"]) > 10:
            raise RedundancyArtifactError("judgment evidence is invalid")
        for item in row["evidence"]:
            if not isinstance(item, Mapping) or set(item) != {"candidate_quote", "matched_id", "matched_quote"} or item.get("matched_id") not in matched_ids:
                raise RedundancyArtifactError("judgment evidence object is invalid")
            matched_text = str(evidence_by_id[str(item["matched_id"])].get("text") or evidence_by_id[str(item["matched_id"])].get("page_content") or "")
            if not isinstance(item.get("candidate_quote"), str) or not item["candidate_quote"] or item["candidate_quote"] not in candidate_text or not isinstance(item.get("matched_quote"), str) or not item["matched_quote"] or item["matched_quote"] not in matched_text:
                raise RedundancyArtifactError("judgment evidence quote is not literal source text")
        if row["relation"] == "equivalent" and (not matched_ids or not row["evidence"] or row["novel_quotes"] or row["conflict_quotes"] or row["attribution_changed"] or row["time_changed"] or row["qualification_changed"]):
            raise RedundancyArtifactError("equivalent judgment lacks conservative evidence requirements")
        if row["relation"] == "equivalent" and any(str(matched_id) in evidence_by_id and (str(evidence_by_id[str(row["candidate_id"])].get("text") or "") != str(evidence_by_id[str(matched_id)].get("text") or "")) and set(candidate_by_id.get(f"{min(str(row['candidate_id']), str(matched_id))}::{max(str(row['candidate_id']), str(matched_id))}", {}).get("guards") or ()) & {"numeric_token_set_changed", "negation_modal_marker_changed"} for matched_id in matched_ids):
            raise RedundancyArtifactError("material-change guard permits no equivalent judgment")
    vector_reference = "vectors/vectors.json" if storage_mode == "shared_input" else "vectors.json"
    if storage_mode == "shared_input" and not (root / vector_reference).is_file():
        raise RedundancyArtifactError("shared-input bundle is missing compact vectors")
    vector_values: dict[str, Any] = {}
    if (root / vector_reference).is_file():
        try:
            vector_payload = json.loads((root / vector_reference).read_text(encoding="utf-8"))
            if not isinstance(vector_payload, Mapping) or not isinstance(vector_payload.get("vectors"), Mapping):
                raise RedundancyArtifactError("bundle vector storage is invalid")
            vector_values = dict(vector_payload.get("vectors") or {})
            if vector_payload.get("scope") and dict(vector_payload.get("scope") or {}) != actual_scope.as_dict():
                raise RedundancyArtifactError("bundle vectors contain a foreign scope")
            representative_ids = {str(row.get("representative_id")) for row in representative_rows}
            if not representative_ids <= set(vector_values or {}):
                raise RedundancyArtifactError("representative map references a missing vector")
            if storage_mode == "shared_input" and set(vector_values or {}) != representative_ids:
                raise RedundancyArtifactError("compact vector rows do not equal the representative set")
            if any(not isinstance(value, list) or not value or any(not isinstance(item, (int, float)) or not math.isfinite(float(item)) for item in value) or not any(float(item) != 0.0 for item in value) for value in (vector_values or {}).values()):
                raise RedundancyArtifactError("bundle contains an invalid vector")
            dimensions = {len(value) for value in (vector_values or {}).values() if isinstance(value, list)}
            if len(dimensions) > 1:
                raise RedundancyArtifactError("bundle vectors have inconsistent dimensions")
        except json.JSONDecodeError as exc:
            raise RedundancyArtifactError("bundle vector storage is invalid") from exc
    expected_vector_digest = _json_hash(vector_values) if vector_values else None
    if manifest.get("logical_vector_digest") != expected_vector_digest:
        raise RedundancyArtifactError("bundle logical vector digest does not match vector storage")
    if storage_mode == "shared_input":
        chroma_manifest_path = root / "vectors" / "chroma_manifest.json"
        if not chroma_manifest_path.is_file():
            raise RedundancyArtifactError("shared-input bundle is missing its vector backend manifest")
        try:
            chroma_manifest = json.loads(chroma_manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RedundancyArtifactError("compact vector backend manifest is invalid") from exc
        if not isinstance(chroma_manifest, Mapping) or chroma_manifest.get("backend") not in {"chroma", "json"}:
            raise RedundancyArtifactError("compact vector backend manifest is invalid")
        if manifest.get("vector_backend") != chroma_manifest.get("backend"):
            raise RedundancyArtifactError("bundle vector backend does not match its backend manifest")
        if chroma_manifest.get("backend") == "json" and chroma_manifest.get("status") != "unavailable":
            raise RedundancyArtifactError("JSON compact vector fallback has an invalid status")
        if chroma_manifest.get("backend") == "chroma":
            if chroma_manifest.get("status") != "created":
                raise RedundancyArtifactError("compact Chroma backend has an invalid status")
            chroma_path = _safe_relative(root / "vectors", chroma_manifest.get("path"))
            if not chroma_path.is_dir() or not chroma_manifest.get("collection"):
                raise RedundancyArtifactError("compact Chroma collection is missing")
            client = None
            try:
                import chromadb
                client = chromadb.PersistentClient(path=str(chroma_path))
                collection = client.get_collection(name=str(chroma_manifest["collection"]))
                collection_metadata = getattr(collection, "metadata", {}) or {}
                if isinstance(collection_metadata, Mapping) and collection_metadata.get("hnsw:space") not in (None, "cosine"):
                    raise RedundancyArtifactError("compact Chroma collection is not configured for cosine distance")
                stored = collection.get(include=["embeddings", "metadatas"])
            except RedundancyArtifactError:
                raise
            except ModuleNotFoundError as exc:
                raise RedundancyArtifactError("compact Chroma validation requires chromadb") from exc
            except Exception as exc:
                raise RedundancyArtifactError(f"could not validate compact Chroma collection: {type(exc).__name__}") from exc
            finally:
                close_chroma_client(client)
            stored_ids = [str(item) for item in (stored.get("ids") or [])]
            if len(stored_ids) != len(set(stored_ids)) or set(stored_ids) != representative_ids:
                raise RedundancyArtifactError("compact Chroma IDs do not match the representative set")
            raw_stored_vectors = stored.get("embeddings")
            stored_vectors = [] if raw_stored_vectors is None else list(raw_stored_vectors)
            expected_vectors = [vector_values[item] for item in stored_ids]
            if len(stored_vectors) != len(expected_vectors) or any(
                not _stored_vector_matches(actual, expected)
                for actual, expected in zip(stored_vectors, expected_vectors)
            ):
                raise RedundancyArtifactError("compact Chroma vectors do not match the canonical vector sidecar")
            raw_metadatas = stored.get("metadatas")
            stored_metadatas = [] if raw_metadatas is None else list(raw_metadatas)
            if len(stored_metadatas) != len(stored_ids):
                raise RedundancyArtifactError("compact Chroma metadata rows do not match the representative set")
            for stored_id, metadata in zip(stored_ids, stored_metadatas):
                if not isinstance(metadata, Mapping) or any(
                    str(metadata.get(key) or "") != str(actual_scope.as_dict()[key])
                    for key in ("partition_id", "corpus_id", "representation_id")
                ):
                    raise RedundancyArtifactError("compact Chroma metadata has a foreign scope")
                if str(metadata.get("representative_id") or "") != stored_id:
                    raise RedundancyArtifactError("compact Chroma metadata has a mismatched representative ID")
    expected_id = "bundle_" + _json_hash(_identity_for_bundle(manifest))[7:]
    if manifest.get("bundle_id") != expected_id:
        raise RedundancyArtifactError("bundle ID does not match immutable identity")
    if base_export is not None:
        try:
            base_inventory = load_inventory(base_export, inspect_vectors=True)
        except Exception as exc:
            raise RedundancyArtifactError("base export cannot be validated") from exc
        if base_inventory.scope != actual_scope:
            raise RedundancyArtifactError("bundle base scope does not match resolved base export")
        if dict(manifest.get("base_hashes") or {}) != dict(base_inventory.hashes):
            raise RedundancyArtifactError("bundle base hashes do not match the resolved base export")
        for name, digest in (manifest.get("base_hashes") or {}).items():
            path = Path(base_export) / name
            if not path.is_file() or _sha256(path) != digest:
                raise RedundancyArtifactError("base export changed after bundle creation")
        if storage_mode == "full":
            expected_vectors = {str(key): [float(value) for value in vector] for key, vector in base_inventory.vectors.items()}
            if set(vector_values) != set(expected_vectors) or any(
                [float(value) for value in vector_values[key]] != expected_vectors[key]
                for key in expected_vectors
            ):
                raise RedundancyArtifactError("full bundle vectors do not match the resolved base export")
        elif any(
            key not in base_inventory.vectors or [float(value) for value in vector_values[key]] != [float(value) for value in base_inventory.vectors[key]]
            for key in vector_values
        ):
            raise RedundancyArtifactError("compact bundle vectors do not match the resolved base export")
    return {
        "valid": True,
        "manifest": manifest,
        "scope": actual_scope.as_dict(),
        "evidence_count": len(evidence),
        "representative_count": len(representative_rows),
        "candidate_count": len(candidate_rows),
        "judgment_count": len(judgment_rows),
        "artifact_count": len(manifest.get("artifacts") or {}),
        "review": {
            "candidate_preview": candidate_rows[:50],
            "judgment_preview": judgment_rows[:50],
            "representative_preview": representative_rows[:50],
            "coverage": dict(manifest.get("coverage") or {}),
            "storage_mode": manifest.get("storage_mode"),
            "vector_backend": manifest.get("vector_backend"),
        },
    }


def publish_bundle(private_root: str | Path, bundles_root: str | Path, *, base_export: str | Path | None = None, partition_lock: Any | None = None) -> dict[str, Any]:
    source = Path(private_root).expanduser().resolve()
    validation = validate_bundle(source, base_export=base_export)
    destination = Path(bundles_root).expanduser().resolve() / str(validation["manifest"]["bundle_id"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing = validate_bundle(destination)
        if existing["manifest"].get("bundle_id") == validation["manifest"].get("bundle_id"):
            return {"status": "reused", "path": str(destination), "bundle_id": validation["manifest"]["bundle_id"]}
        raise RedundancyArtifactError("bundle target already exists with different content")
    temporary = destination.with_name(destination.name + ".tmp")
    if temporary.exists():
        raise RedundancyArtifactError("bundle publication has a stale temporary target")
    acquired = False
    try:
        if partition_lock is not None and hasattr(partition_lock, "acquire"):
            partition_lock.acquire()
            acquired = True
        shutil.copytree(source, temporary)
        validate_bundle(temporary, base_export=base_export)
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    finally:
        if acquired and hasattr(partition_lock, "release"):
            partition_lock.release()
    return {"status": "published", "path": str(destination), "bundle_id": validation["manifest"]["bundle_id"]}


__all__ = ["BUNDLE_CAPABILITIES", "BUNDLE_CONTRACT", "RedundancyArtifactError", "publish_bundle", "validate_bundle", "write_bundle"]
