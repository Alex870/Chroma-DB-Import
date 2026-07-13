from __future__ import annotations

RUNTIME_DEPS_LOADED = False

def load_runtime_deps() -> None:
    """Import heavy runtime dependencies lazily so lightweight commands start fast."""
    global RUNTIME_DEPS_LOADED
    global Chroma, Document, HuggingFaceEmbeddings

    if RUNTIME_DEPS_LOADED:
        return

    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    from langchain_huggingface import HuggingFaceEmbeddings

    RUNTIME_DEPS_LOADED = True
