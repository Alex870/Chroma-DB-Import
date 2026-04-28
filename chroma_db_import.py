from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import re
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


RUNTIME_DEPS_LOADED = False


def load_runtime_deps() -> None:
    global RUNTIME_DEPS_LOADED
    global Chroma, Document, HuggingFaceEmbeddings

    if RUNTIME_DEPS_LOADED:
        return

    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    from langchain_huggingface import HuggingFaceEmbeddings

    RUNTIME_DEPS_LOADED = True


@dataclass
class ImportConfig:
    processed_data_dir: str = "processed_data"
    file_glob: str = "**/*.processed_documents.json"
    state_path: str = "state/chroma_import_state.json"
    persist_dir: str = "chroma_db_raptor_v2"
    collection_name: str = "whisper_rag_v2"
    embedding_model: str = "BAAI/bge-large-en-v1.5"
    import_batch_size: int = 64
    skip_existing_ids: bool = True
    stop_file: str = "state/stop_after_current_import.txt"


def resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path


def load_config(config_path: Path) -> ImportConfig:
    if not config_path.exists():
        return ImportConfig()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    allowed = {field.name for field in fields(ImportConfig)}
    return ImportConfig(**{key: value for key, value in payload.items() if key in allowed})


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = path.with_suffix(f".corrupt.{int(time.time())}.json")
        backup.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        print(f"State file was invalid JSON. Backed it up to {backup} and starting fresh.")
        return {"version": 1, "files": {}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, indent=2, ensure_ascii=True), encoding="utf-8")
    temp_path.replace(path)


def cache_fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"


def has_text(text: str) -> bool:
    return bool(re.sub(r"\s+", " ", text or "").strip())


def is_missing_context_response(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text or "").strip().lower()
    if not compact:
        return False
    asks_for_missing_input = (
        ("please provide" in compact or "please share" in compact or "send me" in compact)
        and any(term in compact for term in ["transcript", "source text", "source material", "podcast text", "material"])
    )
    deferred_until_shared = any(pattern in compact for pattern in ["once shared", "once you share", "when you provide"])
    return asks_for_missing_input or deferred_until_shared


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    clean = {}
    for key, value in metadata.items():
        if value is None:
            clean[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            clean[key] = value
        else:
            clean[key] = json.dumps(value, ensure_ascii=True)
    return clean


def load_processed_documents(cache_path: Path) -> list[Document]:
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    docs = []
    for item in payload.get("documents", []):
        if not isinstance(item, dict):
            continue
        docs.append(
            Document(
                page_content=str(item.get("page_content", "")),
                metadata=dict(item.get("metadata") or {}),
            )
        )
    return docs


def validate_documents(docs: list[Document], label: str) -> None:
    bad = []
    for idx, doc in enumerate(docs):
        metadata = doc.metadata or {}
        node_id = metadata.get("node_id", f"index_{idx}")
        node_type = metadata.get("node_type", "unknown")
        if not has_text(doc.page_content):
            bad.append(f"{node_id} ({node_type}) has empty page_content")
        elif node_type in {"cluster_summary", "episode_thesis", "position_card"} and is_missing_context_response(doc.page_content):
            bad.append(f"{node_id} ({node_type}) has a missing-context response")
        if not metadata.get("node_id"):
            bad.append(f"index_{idx} is missing node_id")
        if not metadata.get("episode_date"):
            bad.append(f"{node_id} ({node_type}) is missing episode_date")
        if node_type in {"leaf_chunk", "cluster_summary", "episode_thesis", "position_card"} and not metadata.get("speaker_scope"):
            bad.append(f"{node_id} ({node_type}) is missing speaker_scope")

    if bad:
        preview = "; ".join(bad[:10])
        if len(bad) > 10:
            preview += f"; and {len(bad) - 10} more"
        raise ValueError(f"{label} contains invalid documents: {preview}")


class ChromaImporter:
    def __init__(self, config: ImportConfig, project_dir: Path):
        self.config = config
        self.project_dir = project_dir
        self.embeddings = HuggingFaceEmbeddings(model_name=config.embedding_model)
        self.vectorstore = Chroma(
            embedding_function=self.embeddings,
            persist_directory=str(resolve_path(project_dir, config.persist_dir)),
            collection_name=config.collection_name,
        )

    def existing_ids(self, ids: list[str]) -> set[str]:
        if not self.config.skip_existing_ids or not ids:
            return set()
        existing: set[str] = set()
        for start in range(0, len(ids), self.config.import_batch_size):
            batch_ids = ids[start : start + self.config.import_batch_size]
            try:
                payload = self.vectorstore.get(ids=batch_ids, include=[])
            except Exception:
                return set()
            existing.update(payload.get("ids") or [])
        return existing

    def import_cache(self, cache_path: Path) -> dict[str, Any]:
        docs = load_processed_documents(cache_path)
        validate_documents(docs, str(cache_path))
        docs = [doc for doc in docs if has_text(doc.page_content)]
        ids = [str(doc.metadata["node_id"]) for doc in docs]
        existing = self.existing_ids(ids)
        pending = [
            Document(page_content=doc.page_content, metadata=sanitize_metadata(doc.metadata))
            for doc in docs
            if str(doc.metadata["node_id"]) not in existing
        ]
        pending_ids = [str(doc.metadata["node_id"]) for doc in docs if str(doc.metadata["node_id"]) not in existing]

        inserted = 0
        for start in range(0, len(pending), self.config.import_batch_size):
            batch_docs = pending[start : start + self.config.import_batch_size]
            batch_ids = pending_ids[start : start + self.config.import_batch_size]
            if not batch_docs:
                continue
            self.vectorstore.add_documents(batch_docs, ids=batch_ids)
            inserted += len(batch_docs)
            print(f"    inserted {inserted}/{len(pending)} documents")

        return {"documents": len(docs), "inserted": inserted, "skipped_existing": len(existing)}


def iter_cache_files(processed_data_dir: Path, file_glob: str) -> list[Path]:
    pattern = str(processed_data_dir / file_glob)
    return [Path(path) for path in sorted(glob.glob(pattern, recursive=True)) if Path(path).is_file()]


def run_import(config: ImportConfig, project_dir: Path, one_file: bool) -> int:
    load_runtime_deps()
    processed_data_dir = resolve_path(project_dir, config.processed_data_dir)
    state_path = resolve_path(project_dir, config.state_path)
    stop_file = resolve_path(project_dir, config.stop_file)
    state = load_state(state_path)
    importer = ChromaImporter(config, project_dir)

    files = iter_cache_files(processed_data_dir, config.file_glob)
    pending = []
    for path in files:
        fingerprint = cache_fingerprint(path)
        entry = state.get("files", {}).get(fingerprint)
        if not entry or entry.get("status") != "completed":
            pending.append((path, fingerprint))

    print(f"Found {len(files)} processed cache file(s); {len(pending)} pending import.")
    for idx, (path, fingerprint) in enumerate(pending, 1):
        if stop_file.exists():
            print("Stop file detected before starting next import.")
            break

        print(f"\nFile {idx}/{len(pending)}: {path}")
        try:
            result = importer.import_cache(path)
            state.setdefault("files", {})[fingerprint] = {
                "path": str(path),
                "status": "completed",
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                **result,
            }
            save_state(state_path, state)
            print(f"  completed: inserted={result['inserted']}, skipped_existing={result['skipped_existing']}")
        except Exception as exc:
            state.setdefault("files", {})[fingerprint] = {
                "path": str(path),
                "status": "failed",
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
            }
            save_state(state_path, state)
            raise

        if one_file:
            print("Imported one file; stopping because --one-file was set.")
            break

    print("\nImport complete.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import processed podcast RAG documents into Chroma.")
    parser.add_argument("--config", default="chroma_db_import_config.json", help="Path to the JSON config file.")
    parser.add_argument("--processed-data-dir", help="Override config processed_data_dir.")
    parser.add_argument("--persist-dir", help="Override config persist_dir.")
    parser.add_argument("--collection-name", help="Override config collection_name.")
    parser.add_argument("--one-file", action="store_true", help="Import only one pending cache file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).expanduser()
    project_dir = config_path.resolve().parent if config_path.exists() else Path.cwd()
    config = load_config(config_path)
    if args.processed_data_dir:
        config.processed_data_dir = args.processed_data_dir
    if args.persist_dir:
        config.persist_dir = args.persist_dir
    if args.collection_name:
        config.collection_name = args.collection_name
    return run_import(config, project_dir, args.one_file)


if __name__ == "__main__":
    raise SystemExit(main())
