"""Corpus-release-v1 plans and an atomic local release lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .representation import (
    QWEN3_MODEL,
    QWEN3_MODEL_REVISION,
    QWEN3_PROFILE,
    QWEN3_QUERY_INSTRUCTION_PROFILE,
)

CONTRACT = "corpus-release-v1"
MUTABLE = {"notes", "display_label", "ui_state", "release_id", "plan_id"}


class ReleaseError(ValueError):
    pass



def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _id(value: Mapping[str, Any], prefix: str) -> str:
    payload = {key: deepcopy(item) for key, item in value.items() if key not in MUTABLE}
    return f"{prefix}_{hashlib.sha256(_canonical(payload)).hexdigest()}"


def export_fingerprint(export_dir: str | Path) -> str:
    root = Path(export_dir).resolve()
    digest = hashlib.sha256()
    for required in ("podcast.json", "import_manifest.json", "chroma.sqlite3"):
        if not (root / required).is_file():
            raise ReleaseError(f"export is missing required file: {required}")
    files=sorted((path for path in root.rglob("*") if path.is_file()),key=lambda path:path.relative_to(root).as_posix())
    for path in files:
        name=path.relative_to(root).as_posix()
        if not path.is_file():
            raise ReleaseError(f"export is missing required file: {name}")
        digest.update(name.encode("utf-8"))
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def export_representation_id(export_dir: str | Path) -> str:
    path = Path(export_dir).resolve() / "import_manifest.json"
    if not path.is_file():
        raise ReleaseError("export is missing required file: import_manifest.json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    value = str(manifest.get("representation_id") or (manifest.get("representation") or {}).get("representation_id") or "")
    if not value:
        raise ReleaseError("export import manifest is missing its vector representation identity")
    return value


def inspect_export_work(export_dir: str | Path) -> dict[str, Any]:
    """Return measured export size and importer work evidence for release planning."""
    root = Path(export_dir).resolve()
    manifest_path = root / "import_manifest.json"
    if not manifest_path.is_file():
        raise ReleaseError("export is missing required file: import_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = [path for path in root.rglob("*") if path.is_file()]
    reconciliation = manifest.get("reconciliation") or {}
    cache = manifest.get("embedding_cache") or {}
    operation = manifest.get("operation") or {}
    elapsed = operation.get("elapsed_seconds")
    return {
        "vector_work": {
            "add": len(reconciliation.get("added") or []),
            "update": len(reconciliation.get("changed") or []),
            "metadata_only": len(reconciliation.get("metadata_only") or []),
            "remove": len(reconciliation.get("removed") or []),
            "unchanged": len(reconciliation.get("unchanged") or []),
        },
        "cache_reuse": {
            "embedding_hits": int(cache.get("hits") or cache.get("hit_count") or 0),
            "embedding_misses": int(cache.get("misses") or cache.get("miss_count") or 0),
        },
        "storage": {
            "export_bytes": sum(path.stat().st_size for path in files),
            "database_bytes": (root / "chroma.sqlite3").stat().st_size if (root / "chroma.sqlite3").is_file() else 0,
            "file_count": len(files),
        },
        "duration": {
            "seconds": float(elapsed) if isinstance(elapsed, (int, float)) and elapsed >= 0 else None,
            "basis": "measured_import" if isinstance(elapsed, (int, float)) and elapsed >= 0 else "unavailable_for_legacy_export",
        },
    }


def plan_release(delta: Mapping[str, Any], *, parent_release_id: str | None,
                 active_embedding: Mapping[str, Any] | None, requested_embedding: Mapping[str, Any],
                 selection_fingerprint: str, source_cache_ids: list[str] | None = None,
                 removal_mode: str = "retain_advisory", export_bundle_fingerprint: str = "",
                 vector_representation_id: str = "", export_work: Mapping[str, Any] | None = None,
                 lexical_corpus: str | Path | None = None) -> dict[str, Any]:
    if removal_mode not in {"retain_advisory", "reconcile_approved"}:
        raise ReleaseError("invalid removal mode")
    if delta.get("contract_version") != "processed-delta-v1" or not delta.get("delta_id"):
        raise ReleaseError("a validated processed-delta-v1 is required")
    embedding_model = str(requested_embedding.get("model") or requested_embedding.get("model_id") or "")
    if embedding_model != QWEN3_MODEL:
        raise ReleaseError(f"only {QWEN3_MODEL} releases are supported")
    if requested_embedding.get("model_revision") != QWEN3_MODEL_REVISION:
        raise ReleaseError("release embedding identity must use the pinned Qwen3 revision")
    if requested_embedding.get("dimension") != 2560 and requested_embedding.get("dimensions") != 2560:
        raise ReleaseError("release embedding identity must declare dimension 2560")
    if requested_embedding.get("provider") not in (None, "sentence_transformers"):
        raise ReleaseError("release embedding provider must be sentence_transformers")
    if requested_embedding.get("normalize_embeddings") not in (None, True):
        raise ReleaseError("release embeddings must be normalized")
    if requested_embedding.get("distance_metric") not in (None, "cosine"):
        raise ReleaseError("release distance metric must be cosine")
    if requested_embedding.get("profile") not in (None, QWEN3_PROFILE):
        raise ReleaseError("release embedding profile must be the Qwen3 production profile")
    if requested_embedding.get("query_instruction_profile") not in (None, QWEN3_QUERY_INSTRUCTION_PROFILE):
        raise ReleaseError("release query instruction profile must be podcast-retrieval-v1")
    mismatch = active_embedding is not None and active_embedding != requested_embedding
    added = len(delta.get("added_document_ids", [])); changed = len(delta.get("changed_document_ids", []))
    removed = len(delta.get("removed_document_ids", []))
    plan: dict[str, Any] = {
        "contract_version": CONTRACT,
        "producer": {"name": "chroma-db-import", "contract_version": "1"},
        "parent_release_id": parent_release_id,
        "source_cache_ids": sorted(source_cache_ids or delta.get("parent_cache_ids", [])),
        "applied_delta_ids": [delta["delta_id"]],
        "representation_fingerprint": delta["representation_fingerprint"],
        "vector_representation_id": str(vector_representation_id),
        "embedding_identity": dict(requested_embedding),
        "selection_fingerprint": selection_fingerprint,
        "export_bundle_fingerprint": str(export_bundle_fingerprint),
        "vector_operations": {
            "add": added,
            "update": changed,
            "remove_advisory": removed if removal_mode == "retain_advisory" else 0,
            "remove_approved": removed if removal_mode == "reconcile_approved" else 0,
        },
        "delta_effects": {
            "added_document_ids": sorted(map(str, delta.get("added_document_ids", []))),
            "changed_document_ids": sorted(map(str, delta.get("changed_document_ids", []))),
            "removed_document_ids": sorted(map(str, delta.get("removed_document_ids", []))),
            "stale_judgment_ids": sorted(map(str, delta.get("stale_judgment_ids", []))),
        },
        "removal_mode": removal_mode,
        "smoke_tests": [],
        "work_estimate": deepcopy(dict(export_work or {})),
        "rollback_release_id": parent_release_id,
        "retention_state": "planned",
        "separate_release_required": mismatch,
        "legacy_reduced_evaluability": False,
        "validation": {"delta_counts_reconciled": True},
    }
    if lexical_corpus:
        from .lexical_index import inspect_corpus
        evidence=inspect_corpus(lexical_corpus)
        plan["retrieval_channels"]=[{"type":"lexical","contract_version":"lexical-index-v1","relative_location":"lexical-index.json",**evidence}]
    else:
        plan["retrieval_channels"]=[]
    plan["release_id"] = _id(plan, "release")
    plan["plan_id"] = _id({"release": plan, "action": "stage_and_promote"}, "release_plan")
    validate_release(plan)
    return plan


def wrap_legacy_export(metadata: Mapping[str, Any]) -> dict[str, Any]:
    wrapped = dict(metadata)
    wrapped.setdefault("contract_version", CONTRACT)
    wrapped.setdefault("release_id", None)
    wrapped["legacy_reduced_evaluability"] = True
    return wrapped


def validate_release(release: Mapping[str, Any]) -> None:
    if release.get("contract_version") != CONTRACT:
        raise ReleaseError("unsupported corpus release contract")
    if release.get("release_id") != _id(release, "release"):
        raise ReleaseError("corpus release identity mismatch")
    embedding = release.get("embedding_identity") or {}
    model = str(embedding.get("model") or embedding.get("model_id") or "")
    if model != QWEN3_MODEL:
        raise ReleaseError(f"release declares unsupported embedding model: {model or '<missing>'}")
    if embedding.get("model_revision") != QWEN3_MODEL_REVISION:
        raise ReleaseError("release does not declare the pinned Qwen3 revision")
    if embedding.get("dimension", embedding.get("dimensions")) != 2560:
        raise ReleaseError("release does not declare embedding dimension 2560")
    if embedding.get("provider", "sentence_transformers") != "sentence_transformers":
        raise ReleaseError("release embedding provider must be sentence_transformers")
    if embedding.get("normalize_embeddings", True) is not True:
        raise ReleaseError("release embeddings must be normalized")
    if embedding.get("distance_metric", "cosine") != "cosine":
        raise ReleaseError("release distance metric must be cosine")
    ops = release.get("vector_operations", {})
    if any(not isinstance(ops.get(key), int) or ops[key] < 0 for key in ("add", "update", "remove_advisory")):
        raise ReleaseError("invalid vector operation counts")
    if "remove_approved" in ops and (not isinstance(ops["remove_approved"], int) or ops["remove_approved"] < 0):
        raise ReleaseError("invalid approved removal count")


def add_release_to_podcast(podcast: Mapping[str, Any], release_id: str | None) -> dict[str, Any]:
    result = dict(podcast)
    if release_id is not None:
        result["corpus_release_id"] = release_id
    return result


class ReleaseStore:
    def __init__(self, root: str | Path, *, retain: int = 3):
        self.root = Path(root); self.retain = retain
        self.releases = self.root / "releases"; self.staging = self.root / "staging"
        self.root.mkdir(parents=True, exist_ok=True); self.releases.mkdir(exist_ok=True); self.staging.mkdir(exist_ok=True)

    @property
    def active_file(self) -> Path: return self.root / "active-release.json"

    def active(self) -> str | None:
        if not self.active_file.exists(): return None
        return json.loads(self.active_file.read_text(encoding="utf-8"))["release_id"]

    def active_path(self) -> Path | None:
        release_id = self.active()
        return self.releases / release_id / "export" if release_id else None

    def load_release(self, release_id: str) -> dict[str, Any]:
        path = self.releases / str(release_id) / "release.json"
        if not path.is_file():
            raise ReleaseError(f"release record is unavailable: {release_id}")
        value = json.loads(path.read_text(encoding="utf-8"))
        validate_release(value)
        return value

    def stage(self, plan: Mapping[str, Any], payload: Mapping[str, Any], *, cancel: bool = False) -> Path:
        validate_release(plan)
        destination = self.staging / str(plan["release_id"])
        if cancel: return destination
        if destination.exists(): shutil.rmtree(destination)
        destination.mkdir(parents=True)
        (destination / "release.json").write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (destination / "payload.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return destination

    def stage_export(self, plan: Mapping[str, Any], export_dir: str | Path, *, cancel: bool = False, lexical_corpus: str | Path | None = None) -> Path:
        validate_release(plan)
        source = Path(export_dir).resolve()
        destination = self.staging / str(plan["release_id"])
        if cancel:
            return destination
        podcast_path = source / "podcast.json"
        manifest_path = source / "import_manifest.json"
        chroma_path = source / "chroma.sqlite3"
        missing = [str(path.name) for path in (podcast_path, manifest_path, chroma_path) if not path.is_file()]
        if missing:
            raise ReleaseError(f"export is missing required files: {', '.join(missing)}")
        podcast = json.loads(podcast_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_export_fingerprint = str(plan.get("export_bundle_fingerprint") or "")
        if not expected_export_fingerprint:
            raise ReleaseError("release plan is not bound to an export bundle fingerprint")
        if export_fingerprint(source) != expected_export_fingerprint:
            raise ReleaseError("export bundle fingerprint does not match release plan")
        from .contract import validate_podcast_metadata
        podcast_validation = validate_podcast_metadata(podcast)
        if not podcast_validation.valid:
            raise ReleaseError("podcast metadata is invalid: " + "; ".join(podcast_validation.errors[:3]))
        if (manifest.get("validation") or {}).get("valid") is not True:
            raise ReleaseError("import manifest does not prove successful source validation")
        staging = manifest.get("staging") or {}
        if staging.get("valid") is not True:
            raise ReleaseError("import manifest does not prove successful Chroma staging validation")
        if sum(int((plan.get("vector_operations") or {}).get(key) or 0) for key in ("add", "update")) and not staging.get("smoke_query_ids"):
            raise ReleaseError("import manifest does not contain a successful pinned retrieval smoke query")
        representation_id = str(manifest.get("representation_id") or (manifest.get("representation") or {}).get("representation_id") or "")
        podcast_representation_id = str(podcast.get("representation_id") or "")
        if podcast_representation_id != representation_id:
            raise ReleaseError("podcast representation_id does not match import manifest")
        expected_vector_representation = str(plan.get("vector_representation_id") or plan.get("representation_fingerprint") or "")
        if representation_id != expected_vector_representation:
            raise ReleaseError("export vector representation does not match release plan")
        embedding = plan.get("embedding_identity") or {}
        expected_model = str(embedding.get("model") or embedding.get("model_id") or "")
        actual_model = str(manifest.get("embedding_model") or podcast.get("embedding_model") or "")
        if expected_model and actual_model != expected_model:
            raise ReleaseError("export embedding model does not match release plan")
        representation = manifest.get("representation") or {}
        embedding_checks = {
            "profile": representation.get("profile"),
            "model_id": representation.get("model_id") or manifest.get("embedding_model"),
            "provider": representation.get("provider"),
            "dimensions": manifest.get("embedding_dimension") or representation.get("dimension"),
            "dimension": manifest.get("embedding_dimension") or representation.get("dimension"),
            "model_revision": representation.get("model_revision"),
            "output_dimension": representation.get("output_dimension"),
            "normalize_embeddings": representation.get("normalize_embeddings"),
            "distance_metric": representation.get("distance_metric"),
            "contextualization": representation.get("contextualization"),
            "context_header_version": representation.get("context_header_version"),
            "query_instruction_profile": representation.get("query_instruction_profile"),
            "implementation_version": representation.get("implementation_version"),
        }
        for key, actual in embedding_checks.items():
            expected = embedding.get(key)
            if expected not in (None, "") and str(actual or "") != str(expected):
                raise ReleaseError(f"export embedding {key} does not match release plan")
        self._validate_reconciliation(plan, manifest.get("reconciliation") or {})
        operation_mode = str((manifest.get("operation") or {}).get("mode") or "")
        has_removals = bool((plan.get("delta_effects") or {}).get("removed_document_ids"))
        if has_removals and plan.get("removal_mode") == "retain_advisory" and operation_mode == "reconcile":
            raise ReleaseError("advisory removals cannot be staged from a destructive reconcile export")
        if has_removals and plan.get("removal_mode") == "reconcile_approved" and operation_mode != "reconcile":
            raise ReleaseError("approved removals require an export produced in reconcile mode")
        if destination.exists():
            shutil.rmtree(destination)
        export_target = destination / "export"
        shutil.copytree(source, export_target)
        release_id = str(plan["release_id"])
        podcast["corpus_release_id"] = release_id
        manifest["corpus_release_id"] = release_id
        manifest["applied_delta_ids"] = list(plan.get("applied_delta_ids") or [])
        manifest["corpus_release"] = {"release_id": release_id, "record": "release.json"}
        (export_target / "podcast.json").write_text(json.dumps(podcast, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (export_target / "import_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (destination / "release.json").write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (export_target / "release.json").write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        channels=list(plan.get("retrieval_channels") or [])
        if channels:
            if len(channels)!=1 or channels[0].get("type")!="lexical" or not lexical_corpus:
                raise ReleaseError("release plan requires its lexical representation corpus during staging")
            from .lexical_index import build_sidecar, validate_sidecar
            sidecar=build_sidecar(lexical_corpus,export_target/channels[0]["relative_location"],parent_release_id=release_id)
            validate_sidecar(sidecar,release_id=release_id,expected=channels[0])
            try:
                import chromadb
                client=chromadb.PersistentClient(path=str(export_target)); collection=client.get_collection(str(podcast["collection_name"])); dense_ids=sorted(map(str,collection.get(include=[]).get("ids") or [])); client._system.stop(); chromadb.api.client.SharedSystemClient.clear_system_cache()
            except Exception as exc:
                raise ReleaseError(f"could not validate lexical alignment against staged Chroma: {exc}") from exc
            if dense_ids!=sorted(sidecar["ordered_document_ids"]):
                raise ReleaseError("lexical sidecar document IDs do not exactly align with staged dense collection")
            manifest["retrieval_channels"]=[{**channels[0],"channel_id":sidecar["channel_id"],"checksum":sidecar["checksum"]}]
            podcast["retrieval_channels"]=manifest["retrieval_channels"]
            (export_target / "podcast.json").write_text(json.dumps(podcast, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            (export_target / "import_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (destination / "staging-validation.json").write_text(json.dumps({
            "release_id": release_id,
            "export_bundle_fingerprint": expected_export_fingerprint,
            "delta_counts_reconciled": True,
            "podcast_metadata_valid": True,
            "chroma_staging_valid": True,
            "smoke_query_ids": list(staging.get("smoke_query_ids") or []),
        }, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return destination

    @staticmethod
    def _validate_reconciliation(plan: Mapping[str, Any], reconciliation: Mapping[str, Any]) -> None:
        effects = plan.get("delta_effects") or {}
        expected_added = set(map(str, effects.get("added_document_ids") or []))
        expected_changed = set(map(str, effects.get("changed_document_ids") or []))
        expected_removed = set(map(str, effects.get("removed_document_ids") or []))
        actual_added = set(map(str, reconciliation.get("added") or []))
        actual_changed = set(map(str, reconciliation.get("changed") or []))
        actual_changed.update(map(str, reconciliation.get("metadata_only") or []))
        actual_removed = set(map(str, reconciliation.get("removed") or []))
        if expected_added != actual_added or expected_changed != actual_changed:
            raise ReleaseError("export reconciliation does not match processed delta add/update effects")
        if expected_removed != actual_removed:
            raise ReleaseError("export reconciliation does not match processed delta removal effects")

    def promote(self, plan: Mapping[str, Any], *, approved_plan_id: str) -> str:
        validate_release(plan)
        if approved_plan_id != plan.get("plan_id"):
            raise ReleaseError("human approval does not match release plan")
        release_id = str(plan["release_id"]); staged = self.staging / release_id
        if not staged.exists(): raise ReleaseError("release is not staged")
        if not (staged / "export" / "podcast.json").is_file():
            raise ReleaseError("staged release is not a consumer-readable Chroma export")
        destination = self.releases / release_id
        if destination.exists():
            existing_path = destination / "release.json"
            if not existing_path.is_file():
                raise ReleaseError("existing release is missing its immutable release record")
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            validate_release(existing)
            if existing != dict(plan):
                raise ReleaseError("existing release record does not match approved release plan")
            shutil.rmtree(staged)
        else:
            os.replace(staged, destination)
        temporary = self.root / ".active-release.tmp"
        temporary.write_text(json.dumps({"release_id": release_id}, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.active_file)
        return release_id

    def rollback(self, release_id: str, *, approved_plan_id: str, rollback_plan_id: str) -> str:
        expected = _id({"action": "rollback", "target_release_id": release_id}, "rollback_plan")
        if rollback_plan_id != expected or approved_plan_id != expected:
            raise ReleaseError("human approval does not match rollback plan")
        if not (self.releases / release_id / "release.json").exists():
            raise ReleaseError("rollback release is unavailable")
        if not (self.releases / release_id / "export" / "podcast.json").exists():
            raise ReleaseError("rollback release is not consumer-readable")
        temporary = self.root / ".active-release.tmp"
        temporary.write_text(json.dumps({"release_id": release_id}, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.active_file); return release_id

    @staticmethod
    def rollback_plan_id(release_id: str) -> str:
        return _id({"action": "rollback", "target_release_id": release_id}, "rollback_plan")

    def retention_candidates(self, *, known_good: str = "") -> list[str]:
        items = sorted(self.releases.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        keep = {item.name for item in items[:self.retain]}; keep.add(known_good); keep.add(self.active() or "")
        return [item.name for item in items if item.name not in keep]

    def prune_plan_id(self, release_ids: list[str]) -> str:
        return _id({"action": "prune", "release_ids": sorted(release_ids)}, "prune_plan")

    def prune(self, release_ids: list[str], *, approved_plan_id: str) -> list[str]:
        release_ids = sorted(set(map(str, release_ids)))
        expected = self.prune_plan_id(release_ids)
        if approved_plan_id != expected:
            raise ReleaseError("human approval does not match retention deletion plan")
        active = self.active()
        if active in release_ids:
            raise ReleaseError("active release cannot be pruned")
        deleted = []
        for release_id in release_ids:
            path = self.releases / release_id
            if path.exists():
                shutil.rmtree(path)
                deleted.append(release_id)
        return deleted
