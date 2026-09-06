"""Package entrypoints for the Chroma DB Import project."""

from chroma_db_import.config import ImportConfig
from chroma_db_import.importer import ChromaImporter, should_include_document
from chroma_db_import.representation import RepresentationSpec
from chroma_db_import.managed import ContextIdentity, ManagedCatalog, ManagedContextError
from chroma_db_import.deduplication import DedupInput, DedupPlan, resolve_dedup_policy
from chroma_db_import.redundancy_models import AnalysisUnit, CandidatePair, Coverage, Judgment, Scope, SelectionResult
from chroma_db_import.redundancy_policy import resolve_redundancy_policy


def main() -> int:
    """Lazily invoke the CLI without importing it during package startup."""
    from chroma_db_import.cli import main as cli_main

    return cli_main()


__all__ = [
    "ChromaImporter",
    "ContextIdentity",
    "ImportConfig",
    "ManagedCatalog",
    "ManagedContextError",
    "DedupInput",
    "DedupPlan",
    "RepresentationSpec",
    "main",
    "should_include_document",
    "resolve_dedup_policy",
    "AnalysisUnit",
    "CandidatePair",
    "Coverage",
    "Judgment",
    "Scope",
    "SelectionResult",
    "resolve_redundancy_policy",
]
