from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Mapping

from chroma_db_import.asset_filters import normalize_asset_filter


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class BridgeError(ValueError):
    """A safe, user-facing application error with a stable code."""

    def __init__(self, code: str, message: str, *, field: str | None = None, details_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.details_id = details_id

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "field": self.field, "details_id": self.details_id}


@dataclass(frozen=True)
class ContextRef:
    connection_id: str
    partition_id: str

    def validate(self) -> None:
        if not self.connection_id.strip() or not self.partition_id.strip():
            raise BridgeError("VALIDATION_FAILED", "A connection and partition are required.", field="context")

    def as_dict(self) -> dict[str, str]:
        self.validate()
        return {"connection_id": self.connection_id, "partition_id": self.partition_id}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ContextRef":
        raw = dict(value or {})
        ref = cls(_text(raw.get("connection_id")), _text(raw.get("partition_id")))
        ref.validate()
        return ref


@dataclass(frozen=True)
class ExecutionOptions:
    embedding_device: str = "auto"

    def validate(self) -> None:
        value = self.embedding_device.strip().lower()
        if value != "auto" and value != "cpu" and not (value.startswith("cuda:") and value[5:].isdigit()):
            raise BridgeError("VALIDATION_FAILED", "Embedding device must be auto, cpu, or a CUDA device such as cuda:0.", field="embedding_device")

    def as_dict(self) -> dict[str, str]:
        self.validate()
        return {"embedding_device": self.embedding_device.strip().lower()}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ExecutionOptions":
        options = cls(_text(dict(value or {}).get("embedding_device"), "auto") or "auto")
        options.validate()
        return options


@dataclass(frozen=True)
class Report:
    schema_version: str
    report_id: str
    kind: str
    scope: dict[str, Any]
    generated_at: str
    status: str
    summary: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    STATUSES: ClassVar[set[str]] = {"pass", "warnings", "partial", "failed", "unavailable"}

    def validate(self) -> None:
        if self.schema_version != "gui-report-v1":
            raise BridgeError("VALIDATION_FAILED", "Unsupported GUI report schema.", field="schema_version")
        if not self.report_id.strip() or not self.kind.strip():
            raise BridgeError("VALIDATION_FAILED", "Report identity is required.")
        if self.status not in self.STATUSES:
            raise BridgeError("VALIDATION_FAILED", "Report status is invalid.", field="status")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "kind": self.kind,
            "scope": dict(self.scope),
            "generated_at": self.generated_at,
            "status": self.status,
            "summary": dict(self.summary),
            "findings": [dict(item) for item in self.findings],
            "details": dict(self.details),
        }


def make_report(kind: str, scope: Mapping[str, Any], *, status: str = "pass", summary: Mapping[str, Any] | None = None, findings: list[Mapping[str, Any]] | None = None, details: Mapping[str, Any] | None = None, report_id: str | None = None) -> dict[str, Any]:
    report = Report(
        schema_version="gui-report-v1", report_id=report_id or new_id("report"), kind=kind,
        scope=dict(scope), generated_at=utc_now(), status=status,
        summary=dict(summary or {}), findings=[dict(item) for item in findings or []], details=dict(details or {}),
    )
    return report.as_dict()


def _text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


@dataclass
class SelectionPolicy:
    speaker_mode: str = "all"
    excluded_speakers: list[str] = field(default_factory=list)
    allowlist_speakers: list[str] = field(default_factory=list)
    episode_overrides: dict[str, list[str]] = field(default_factory=dict)
    excluded_episode_ids: list[str] = field(default_factory=list)
    asset_filter: str = "reviewed"
    asset_pattern: str = ""

    def validate(self) -> None:
        if self.speaker_mode not in {"all", "allowlist"}:
            raise BridgeError("VALIDATION_FAILED", "Speaker mode must be all or allowlist.", field="speaker_mode")
        try:
            self.asset_filter = normalize_asset_filter(self.asset_filter)
        except ValueError as exc:
            raise BridgeError("VALIDATION_FAILED", str(exc), field="asset_filter") from exc
        if self.asset_filter == "custom" and not self.asset_pattern.strip():
            raise BridgeError("VALIDATION_FAILED", "A filename pattern is required for the custom asset filter.", field="asset_pattern")
        self.excluded_speakers = sorted({_text(value) for value in self.excluded_speakers if _text(value)})
        self.allowlist_speakers = sorted({_text(value) for value in self.allowlist_speakers if _text(value)})
        self.excluded_episode_ids = sorted({_text(value) for value in self.excluded_episode_ids if _text(value)})
        self.episode_overrides = {
            _text(key): sorted({_text(value) for value in values if _text(value)})
            for key, values in self.episode_overrides.items()
            if _text(key)
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "SelectionPolicy":
        raw = dict(value or {})
        policy = cls(
            speaker_mode=_text(raw.get("speaker_mode"), "all") or "all",
            excluded_speakers=list(raw.get("excluded_speakers") or []),
            allowlist_speakers=list(raw.get("allowlist_speakers") or []),
            episode_overrides={str(k): list(v or []) for k, v in dict(raw.get("episode_overrides") or {}).items()},
            excluded_episode_ids=list(raw.get("excluded_episode_ids") or []),
            asset_filter=_text(raw.get("asset_filter"), "reviewed") or "reviewed",
            asset_pattern=_text(raw.get("asset_pattern")),
        )
        policy.validate()
        return policy

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        return stable_hash(self.as_dict())


@dataclass
class DatabaseRecord:
    id: str
    display_name: str
    source_kind: str
    source_ref: dict[str, Any]
    target: dict[str, Any]
    downstream_identity: dict[str, Any] | None = None
    selection_policy: dict[str, Any] = field(default_factory=lambda: SelectionPolicy().as_dict())
    execution_options: dict[str, Any] = field(default_factory=lambda: ExecutionOptions().as_dict())
    settings_revision: int = 1
    archived: bool = False
    last_check: dict[str, Any] | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    VALID_SOURCE_KINDS: ClassVar[set[str]] = {"folder", "managed"}

    def validate(self) -> None:
        if not self.id:
            raise BridgeError("VALIDATION_FAILED", "Database ID is required.", field="id")
        if not self.display_name.strip():
            raise BridgeError("VALIDATION_FAILED", "Database name is required.", field="display_name")
        if self.source_kind not in self.VALID_SOURCE_KINDS:
            raise BridgeError("VALIDATION_FAILED", "Source kind must be folder or managed.", field="source_kind")
        if not isinstance(self.source_ref, dict) or not self.source_ref:
            raise BridgeError("VALIDATION_FAILED", "A source reference is required.", field="source_ref")
        if not isinstance(self.target, dict) or not self.target.get("path"):
            raise BridgeError("VALIDATION_FAILED", "A resolved target path is required.", field="target")
        SelectionPolicy.from_mapping(self.selection_policy)
        self.execution_options = ExecutionOptions.from_mapping(self.execution_options).as_dict()
        if self.settings_revision < 1:
            raise BridgeError("VALIDATION_FAILED", "Settings revision must be positive.", field="settings_revision")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DatabaseRecord":
        record = cls(
            id=_text(value.get("id")),
            display_name=_text(value.get("display_name")),
            source_kind=_text(value.get("source_kind")),
            source_ref=dict(value.get("source_ref") or {}),
            target=dict(value.get("target") or {}),
            downstream_identity=dict(value.get("downstream_identity") or {}) or None,
            selection_policy=dict(value.get("selection_policy") or SelectionPolicy().as_dict()),
            execution_options=dict(value.get("execution_options") or ExecutionOptions().as_dict()),
            settings_revision=int(value.get("settings_revision") or 1),
            archived=bool(value.get("archived", False)),
            last_check=dict(value.get("last_check") or {}) or None,
            created_at=_text(value.get("created_at"), utc_now()) or utc_now(),
            updated_at=_text(value.get("updated_at"), utc_now()) or utc_now(),
        )
        record.validate()
        return record


@dataclass
class PreviewEffects:
    episodes_total: int = 0
    records_total: int = 0
    insert_ids: list[str] = field(default_factory=list)
    replace_ids: list[str] = field(default_factory=list)
    metadata_only_ids: list[str] = field(default_factory=list)
    unchanged_ids: list[str] = field(default_factory=list)
    retained_missing_ids: list[str] = field(default_factory=list)
    delete_ids: list[str] = field(default_factory=list)
    episode_changes: list[dict[str, Any]] = field(default_factory=list)
    delete_episodes: list[dict[str, Any]] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)

    @property
    def writes(self) -> int:
        return len(self.insert_ids) + len(self.replace_ids) + len(self.metadata_only_ids)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["writes"] = self.writes
        return payload


@dataclass
class FrozenPreview:
    preview_id: str
    operation: str
    database_id: str | None
    draft_id: str | None
    created_at: str
    settings_revision: int
    settings_hash: str
    source_snapshot: dict[str, Any]
    target_identity: dict[str, Any]
    selection_policy: dict[str, Any]
    representation: dict[str, Any]
    validation_findings: list[dict[str, Any]]
    effects: PreviewEffects
    required_acknowledgments: list[str] = field(default_factory=list)
    status: str = "ready"
    schema_version: str = "gui-preview-v1"
    # For an existing database, this is the hash of the registered settings
    # that were current when the preview was prepared.  The requested
    # selection may intentionally differ for a one-run review, so validation
    # must compare the database against this base hash rather than treating
    # the preview policy as a persisted settings change.
    base_settings_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["effects"] = self.effects.as_dict()
        payload["preview_hash"] = stable_hash(payload)
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FrozenPreview":
        raw_effects = dict(value.get("effects") or {})
        effects = PreviewEffects(**{key: raw_effects.get(key, default) for key, default in {
            "episodes_total": 0, "records_total": 0, "insert_ids": [], "replace_ids": [],
            "metadata_only_ids": [], "unchanged_ids": [], "retained_missing_ids": [], "delete_ids": [],
            "episode_changes": [], "delete_episodes": [], "reasons": {},
        }.items()})
        preview = cls(
            preview_id=_text(value.get("preview_id")),
            operation=_text(value.get("operation")),
            database_id=_text(value.get("database_id")) or None,
            draft_id=_text(value.get("draft_id")) or None,
            created_at=_text(value.get("created_at"), utc_now()) or utc_now(),
            settings_revision=int(value.get("settings_revision") or 1),
            settings_hash=_text(value.get("settings_hash")),
            source_snapshot=dict(value.get("source_snapshot") or {}),
            target_identity=dict(value.get("target_identity") or {}),
            selection_policy=dict(value.get("selection_policy") or {}),
            representation=dict(value.get("representation") or {}),
            validation_findings=list(value.get("validation_findings") or []),
            effects=effects,
            required_acknowledgments=list(value.get("required_acknowledgments") or []),
            status=_text(value.get("status"), "ready") or "ready",
            schema_version=_text(value.get("schema_version"), "gui-preview-v1") or "gui-preview-v1",
            base_settings_hash=_text(value.get("base_settings_hash")),
        )
        if not preview.preview_id or preview.operation not in {"create", "update", "rebuild", "remove_outdated"}:
            raise BridgeError("VALIDATION_FAILED", "Preview is incomplete or has an unsupported operation.")
        return preview


@dataclass
class JobRecord:
    id: str
    kind: str
    state: str
    stage: str
    database_id: str | None = None
    preview_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    completed_at: str | None = None
    can_cancel: bool = False
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    STATES: ClassVar[set[str]] = {"queued", "running", "succeeded", "succeeded_with_warnings", "failed", "interrupted", "cancelled"}

    def validate(self) -> None:
        if self.state not in self.STATES:
            raise BridgeError("VALIDATION_FAILED", f"Unknown job state: {self.state}")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
