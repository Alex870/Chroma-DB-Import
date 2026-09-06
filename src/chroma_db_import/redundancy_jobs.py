"""Catalog-backed frozen semantic-redundancy job helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .managed import ManagedCatalog, ManagedContextError
from .redundancy_policy import policy_fingerprint, resolve_redundancy_policy


JOB_SCHEMA_VERSION = "redundancy-job-v1"


def get_redundancy_policy(catalog: ManagedCatalog, partition_id: str) -> dict[str, Any]:
    return catalog.get_redundancy_policy(partition_id)


def save_redundancy_policy(catalog: ManagedCatalog, partition_id: str, mapping: Mapping[str, Any]) -> str:
    return catalog.save_redundancy_policy(partition_id, dict(mapping))


def create_redundancy_job(
    catalog: ManagedCatalog,
    partition_id: str,
    base_release_id: str,
    *,
    channels: list[str] | tuple[str, ...] | None = None,
    model_artifact_id: str | None = None,
    model_id: str | None = None,
    judge_requested: bool = False,
    prompt_version: str = "redundancy_judge_v1",
    schema_version: str = JOB_SCHEMA_VERSION,
    job_id: str | None = None,
    base_scope: Mapping[str, Any] | None = None,
    base_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if schema_version != JOB_SCHEMA_VERSION:
        raise ManagedContextError(f"unsupported redundancy job schema: {schema_version}")
    policy = catalog.get_redundancy_policy(partition_id)
    chosen_channels = list(channels or [name for name, enabled in (("lexical", policy["policy"].get("lexical_enabled")), ("structural", policy["policy"].get("structural_enabled")), ("dense", policy["policy"].get("dense_enabled"))) if enabled])
    spec = {
        "base_release_id": str(base_release_id),
        "policy": policy["policy"],
        "policy_fingerprint": policy["policy_fingerprint"],
        "channels": sorted(set(chosen_channels)),
        "model_artifact_id": model_artifact_id,
        "model_id": str(model_id) if model_id else None,
        "judge_requested": bool(judge_requested),
        "session_nonce": None if model_artifact_id else __import__("uuid").uuid4().hex,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
    }
    if base_scope is not None:
        spec["base_scope"] = {str(key): value for key, value in sorted(base_scope.items())}
    if base_hashes is not None:
        spec["base_hashes"] = {str(key): str(value) for key, value in sorted(base_hashes.items())}
        spec["base_fingerprint"] = "sha256:" + hashlib.sha256(json.dumps(spec["base_hashes"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return catalog.create_redundancy_job(partition_id, base_release_id, job_id=job_id, spec=spec)


def update_redundancy_job(catalog: ManagedCatalog, job_id: str, *, status: str, detail: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return catalog.update_redundancy_job(job_id, status=status, detail=dict(detail or {}))


def get_redundancy_job(catalog: ManagedCatalog, job_id: str) -> dict[str, Any] | None:
    job = catalog.get_redundancy_job(job_id)
    if job is None:
        return None
    schema_version = str((job.get("spec") or {}).get("schema_version") or JOB_SCHEMA_VERSION)
    if schema_version != JOB_SCHEMA_VERSION:
        raise ManagedContextError(f"unsupported redundancy job schema: {schema_version}")
    return job


__all__ = ["JOB_SCHEMA_VERSION", "create_redundancy_job", "get_redundancy_job", "get_redundancy_policy", "save_redundancy_policy", "update_redundancy_job"]
