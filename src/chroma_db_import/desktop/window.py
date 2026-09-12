from __future__ import annotations

import argparse
import html
import os
from urllib.parse import quote
from pathlib import Path
from typing import Any

from chroma_db_import.workflow.service import WorkflowService

from .bridge import ApplicationBridge


STARTUP_FAILURE = """<!doctype html><meta charset='utf-8'><title>Chroma DB Import setup</title>
<style>body{font-family:system-ui;margin:3rem;max-width:46rem;color:#18202a}code{background:#eef1f4;padding:.15rem .3rem;border-radius:.25rem}</style>
<h1>Database manager is not built</h1><p>{message}</p><p>Run the locked frontend setup/build step, then start the application again.</p>"""


def asset_root() -> Path:
    return Path(__file__).resolve().parent / "assets"


def resolve_state_dir(
    state_dir: Path | None = None,
    *,
    project_root: Path | None = None,
    local_app_data: Path | None = None,
) -> Path:
    """Choose persistent GUI state without requiring writes to a runtime checkout."""
    if state_dir is not None:
        resolved = Path(state_dir).expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    project_state = (project_root or Path.cwd()).expanduser().resolve() / "state" / "gui"
    try:
        project_state.mkdir(parents=True, exist_ok=True)
        return project_state
    except OSError as project_error:
        fallback_root = local_app_data or Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        fallback = fallback_root.expanduser().resolve() / "Chroma DB Import" / "gui"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
        except OSError as fallback_error:
            raise RuntimeError(
                "The Modern UI could not create its GUI state directory. "
                f"Project state failed at {project_state}; per-user state failed at {fallback}."
            ) from fallback_error


def create_window(*, state_dir: Path | None = None, workspace: str = "default") -> Any:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("pywebview is not installed. Install desktop requirements and rebuild the application.") from exc

    service = WorkflowService(resolve_state_dir(state_dir))
    window_ref: dict[str, Any] = {}

    def pick_folder() -> str | None:
        window = window_ref.get("window")
        if window is None:
            return None
        result = window.create_file_dialog(webview.FOLDER_DIALOG)
        return str(result[0]) if result else None

    bridge = ApplicationBridge(service, folder_picker=pick_folder)
    index = asset_root() / "index.html"
    if index.is_file():
        # Pass a plain local path so pywebview can serve the bundle through its
        # loopback server. WebView2 treats a query appended to a file URI as
        # part of the filename on Windows (for example, ``index.html%3F...``).
        # The workspace query is applied after the window has initialized.
        window = webview.create_window(
            "Chroma DB Import",
            url=str(index),
            js_api=bridge,
            width=1280,
            height=820,
            min_size=(960, 640),
            text_select=True,
        )
    else:
        startup_html = STARTUP_FAILURE.replace("{message}", html.escape(f"Frontend assets were not found at {asset_root()}"))
        window = webview.create_window(
            "Chroma DB Import",
            html=startup_html,
            js_api=bridge,
            width=900,
            height=600,
            text_select=True,
        )
    window_ref["window"] = window

    def closing() -> bool:
        active = [job for job in service.list_jobs() if job["state"] in {"queued", "running"} and job["kind"] == "import"]
        if active:
            # pywebview uses a false return to cancel closing. The UI remains
            # available so the worker can reach a safe completion boundary.
            return False
        service.shutdown()
        return True

    try:
        window.events.closing += closing
    except Exception:
        pass
    return window, service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Modern Chroma DB Import database manager")
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--workspace", choices=("default", "contexts"), default="default")
    args = parser.parse_args(argv)
    try:
        import webview
    except ImportError as exc:
        print(f"Modern UI unavailable: {exc}")
        return 2
    window, _service = create_window(state_dir=args.state_dir, workspace=args.workspace)

    def select_workspace() -> None:
        if args.workspace == "contexts":
            workspace_query = quote(args.workspace, safe="")
            window.load_url(f"{window.real_url}?workspace={workspace_query}")

    webview.start(func=select_workspace, debug=False)
    return 0
