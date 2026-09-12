"""UI-independent application workflow services for the modern desktop UI."""

from .models import (
    BridgeError,
    DatabaseRecord,
    FrozenPreview,
    JobRecord,
    PreviewEffects,
    SelectionPolicy,
)
from .catalog import AppCatalog, CatalogError


def __getattr__(name: str):
    """Load the service lazily so leaf workflow modules remain importable."""
    if name == "WorkflowService":
        from .service import WorkflowService

        return WorkflowService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AppCatalog",
    "BridgeError",
    "CatalogError",
    "DatabaseRecord",
    "FrozenPreview",
    "JobRecord",
    "PreviewEffects",
    "SelectionPolicy",
    "WorkflowService",
]
