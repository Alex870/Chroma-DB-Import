"""Small optional-Chroma lifecycle helpers used by redundancy workflows."""

from __future__ import annotations

from typing import Any


def close_chroma_client(client: Any) -> None:
    """Release a private Chroma client without requiring a Chroma import.

    Chroma versions that expose ``close`` should use it. Older clients expose
    only the process-local system-cache clear; that fallback is still safer
    than leaving a Windows SQLite/segment handle alive while a private
    snapshot is removed.
    """

    if client is None:
        return
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
            return
        except Exception:
            pass
    clear_cache = getattr(client, "clear_system_cache", None)
    if callable(clear_cache):
        try:
            clear_cache()
        except Exception:
            pass


__all__ = ["close_chroma_client"]
