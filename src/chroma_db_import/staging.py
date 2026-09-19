from __future__ import annotations

import datetime as dt
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from chroma_db_import.contract import temporal_coverage_stats


@dataclass
class StagingValidation:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    document_count: int = 0
    dimension: int | None = None
    speaker_count: int = 0
    date_count: int = 0
    smoke_query: str = "podcast import pinned retrieval smoke query"
    smoke_query_ids: list[str] = field(default_factory=list)
    temporal_capability: str = "legacy"
    temporal_coverage: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "document_count": self.document_count,
            "dimension": self.dimension,
            "speaker_count": self.speaker_count,
            "date_count": self.date_count,
            "smoke_query": self.smoke_query,
            "smoke_query_ids": self.smoke_query_ids,
            "temporal_capability": self.temporal_capability,
            "temporal_coverage": self.temporal_coverage,
        }


def operation_id(prefix: str = "import") -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def staging_collection_name(collection_name: str, operation: str) -> str:
    """Return a temporary collection name accepted by Chroma.

    Chroma permits only ASCII letters, digits, ``.``, ``_`` and ``-`` and
    requires an alphanumeric first and last character.  Keep the operation
    suffix so concurrent imports still get distinct temporary collections,
    while making the helper safe for both generated and caller-provided IDs.
    """
    safe_collection = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(collection_name)).strip("._-")
    safe_operation = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(operation)).strip("._-")
    collection_part = safe_collection[:48] or "collection"
    operation_part = safe_operation[-24:] or uuid.uuid4().hex[:8]
    return f"stage04-{collection_part}-{operation_part}"


def validate_staged_records(
    ids: list[str],
    documents: list[str],
    metadatas: list[dict[str, Any]],
    embeddings: list[list[float]],
    *,
    expected_dimension: int | None,
    retrieval_ids: list[str] | None = None,
    require_temporal: bool = False,
    legacy_contract: bool = False,
) -> StagingValidation:
    errors: list[str] = []
    warnings: list[str] = []
    if not (len(ids) == len(documents) == len(metadatas) == len(embeddings)):
        errors.append("staging record arrays have inconsistent lengths")
    if len(ids) != len(set(ids)):
        errors.append("staging contains duplicate IDs")
    dimension = expected_dimension
    speaker_count = 0
    date_count = 0
    for index, vector in enumerate(embeddings):
        if not vector:
            errors.append(f"staging vector {index} is empty")
            continue
        if dimension is None:
            dimension = len(vector)
        if len(vector) != dimension:
            errors.append(f"staging vector {index} has dimension {len(vector)}; expected {dimension}")
        if not all(math.isfinite(float(value)) for value in vector):
            errors.append(f"staging vector {index} contains non-finite values")
    for index, metadata in enumerate(metadatas):
        if not isinstance(metadata, dict):
            errors.append(f"staging metadata {index} is not an object")
            continue
        for key, value in metadata.items():
            if not isinstance(value, (str, int, float, bool)):
                errors.append(f"staging metadata {index}.{key} is not a Chroma scalar")
        if not metadata.get("node_id"):
            errors.append(f"staging metadata {index} is missing node_id")
        if not metadata.get("source"):
            errors.append(f"staging metadata {index} is missing primary evidence source")
        if metadata.get("speaker") or metadata.get("speakers"):
            speaker_count += 1
        else:
            warnings.append(f"staging metadata {index} has no speaker coverage")
        if metadata.get("episode_date"):
            date_count += 1
        else:
            warnings.append(f"staging metadata {index} has no date coverage")
    smoke_ids = list(retrieval_ids or ids[:1])
    if ids and not smoke_ids:
        errors.append("pinned retrieval smoke query returned no IDs")
    temporal = temporal_coverage_stats(metadatas)
    if legacy_contract:
        temporal = dict(temporal)
        temporal["temporal_capability"] = "legacy"
    if require_temporal and temporal["temporal_capability"] != "certified":
        errors.append("staging records are not temporally certified")
    return StagingValidation(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        document_count=len(ids),
        dimension=dimension,
        speaker_count=speaker_count,
        date_count=date_count,
        smoke_query_ids=smoke_ids,
        temporal_capability=temporal["temporal_capability"],
        temporal_coverage=temporal,
    )
