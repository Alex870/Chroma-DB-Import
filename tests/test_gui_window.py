from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from chroma_db_import.desktop import window as desktop_window


class _FakeClosingEvent:
    def __init__(self) -> None:
        self.callback = None

    def __iadd__(self, callback):
        self.callback = callback
        return self


class _FakeWindow:
    def __init__(self, *, url: str | None, html: str, js_api: object) -> None:
        self.url = url
        self.html = html
        self.js_api = js_api
        self.events = types.SimpleNamespace(closing=_FakeClosingEvent())
        self.dialog_result = [str(Path("C:/folder with spaces/Ångström/Selected"))]

    def create_file_dialog(self, _dialog_type):
        return self.dialog_result


class DesktopWindowTests(unittest.TestCase):
    def test_default_state_falls_back_to_per_user_location_when_project_state_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            (project_root / "state").write_text("protected runtime state", encoding="utf-8")
            fallback = project_root / "local-app-data"
            resolved = desktop_window.resolve_state_dir(project_root=project_root, local_app_data=fallback)
            self.assertEqual(fallback / "Chroma DB Import" / "gui", resolved)
            self.assertTrue((resolved / "gui_catalog.sqlite3").parent.is_dir())

    def test_packaged_window_routes_picker_and_blocks_close_during_import(self) -> None:
        created: dict[str, _FakeWindow] = {}

        def create_window(title: str, *, url: str | None = None, html: str = "", js_api: object = None, **kwargs):
            self.assertEqual("Chroma DB Import", title)
            self.assertTrue(kwargs["text_select"])
            created["window"] = _FakeWindow(url=url, html=html, js_api=js_api)
            return created["window"]

        fake_webview = types.SimpleNamespace(FOLDER_DIALOG="folder", create_window=create_window)
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"webview": fake_webview}):
            window, service = desktop_window.create_window(state_dir=Path(directory) / "state", workspace="contexts")
            try:
                self.assertIs(window, created["window"])
                self.assertTrue(str(window.url).endswith("index.html"))
                self.assertIn("index.html", str(window.url))
                self.assertNotIn("%3F", str(window.url))

                picker = window.js_api.pick_folder({})
                self.assertTrue(picker["ok"])
                self.assertEqual(str(Path("C:/folder with spaces/Ångström/Selected")), picker["data"])
                window.dialog_result = None
                self.assertIsNone(window.js_api.pick_folder({})["data"])

                service.list_jobs = lambda: [{"kind": "import", "state": "running"}]
                self.assertFalse(window.events.closing.callback())
                service.list_jobs = lambda: []
                self.assertTrue(window.events.closing.callback())
            finally:
                service.shutdown()

    def test_missing_assets_show_setup_message(self) -> None:
        created: dict[str, _FakeWindow] = {}

        def create_window(title: str, *, url: str | None = None, html: str = "", js_api: object = None, **_kwargs):
            created["window"] = _FakeWindow(url=url, html=html, js_api=js_api)
            return created["window"]

        fake_webview = types.SimpleNamespace(FOLDER_DIALOG="folder", create_window=create_window)
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"webview": fake_webview}), patch.object(
            desktop_window, "asset_root", return_value=Path(directory) / "missing-assets"
        ):
            window, service = desktop_window.create_window(state_dir=Path(directory) / "state")
            try:
                self.assertIsNone(window.url)
                self.assertIn("Database manager is not built", window.html)
                self.assertIn("Frontend assets were not found", window.html)
                self.assertIn("locked frontend setup/build step", window.html)
            finally:
                service.shutdown()

    def test_main_applies_context_workspace_after_loopback_initialization(self) -> None:
        loaded_urls: list[str] = []
        fake_window = types.SimpleNamespace(
            real_url="http://127.0.0.1:42001/index.html",
            load_url=loaded_urls.append,
        )

        def start(*, func, **_kwargs):
            func()

        fake_webview = types.SimpleNamespace(start=start)
        with patch.dict(sys.modules, {"webview": fake_webview}), patch.object(
            desktop_window, "create_window", return_value=(fake_window, object())
        ):
            result = desktop_window.main(["--workspace", "contexts"])

        self.assertEqual(0, result)
        self.assertEqual(["http://127.0.0.1:42001/index.html?workspace=contexts"], loaded_urls)


if __name__ == "__main__":
    unittest.main()
