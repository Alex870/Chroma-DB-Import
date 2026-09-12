import os
import unittest
from importlib.util import find_spec
from unittest.mock import patch


@unittest.skipUnless(find_spec("PySide6"), "PySide6 is not installed in this environment")
class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from chroma_db_import.ui_window import MainWindow

        self.load_state_patch = patch.object(MainWindow, "load_persistent_state", lambda _window: None)
        self.save_state_patch = patch.object(MainWindow, "save_persistent_state", lambda _window: None)
        self.load_state_patch.start()
        self.save_state_patch.start()

    def tearDown(self):
        self.save_state_patch.stop()
        self.load_state_patch.stop()

    def test_main_window_constructs_with_frontier_controls(self):
        from chroma_db_import.ui_window import MainWindow
        window = MainWindow()
        self.assertEqual(window.mirror_removals.text(), "Mirror records removed from source caches")
        self.assertEqual(window.representation_profile.count(), 1)
        self.assertEqual(window.representation_profile.currentData(), "qwen3-embedding-4b-shadow")
        self.assertIn("minimal", [window.contextualization.itemText(index) for index in range(window.contextualization.count())])
        window.close()

    def test_switching_contexts_panel_does_not_retain_deleted_cuda_button(self):
        from chroma_db_import.ui_window import MainWindow

        window = MainWindow()
        window.render_contexts()
        self.app.processEvents()
        window.update_action_states()
        self.assertIsNone(window.cuda_install_button)
        window.render_global()
        self.app.processEvents()
        window.update_action_states()
        self.assertIsNotNone(window.cuda_install_button)
        window.close()

    def test_switching_to_contexts_preserves_embedding_controls(self):
        from chroma_db_import.ui_window import MainWindow

        window = MainWindow()
        window.render_contexts()
        self.app.processEvents()
        config = window._current_managed_import_config()
        self.assertEqual("qwen3-embedding-4b-shadow", config.representation_profile)
        self.assertEqual("Qwen/Qwen3-Embedding-4B", config.embedding_model)
        window.close()

    def test_failure_dialog_can_be_constructed(self):
        from PySide6.QtWidgets import QDialog
        from chroma_db_import.ui_window import MainWindow

        window = MainWindow()
        with patch("chroma_db_import.ui_window.QDialog.exec", return_value=QDialog.Accepted):
            window.show_copyable_message("Import failed", "The underlying operation failed.")
        window.close()

    def test_reconcile_action_is_disabled_until_prerequisites_exist(self):
        from chroma_db_import.ui_window import MainWindow

        window = MainWindow()
        self.assertFalse(window.generate_action.isEnabled())
        self.assertFalse(window.update_action.isEnabled())
        self.assertFalse(window.reconcile_action.isEnabled())
        self.assertIn("Requires:", window.reconcile_action.toolTip())
        window.close()

    def test_open_processed_folder_returns_after_loading_assets(self):
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from PySide6.QtWidgets import QDialog

        from chroma_db_import.ui_models import Episode
        from chroma_db_import.ui_window import MainWindow

        window = MainWindow()
        episode = Episode(
            path=Path("fixture.processed_documents.json"),
            fingerprint="fixture-fingerprint",
            title="Fixture episode",
            episode_id="fixture",
            episode_date="2026-01-01",
            documents=[],
            speakers=[],
            node_counts={},
        )
        window.loader.last_selection = SimpleNamespace(selected_count=1, discovered_count=1)
        with patch("chroma_db_import.ui_window.ProcessedFolderPreviewDialog") as dialog_class:
            dialog = dialog_class.return_value
            dialog.exec.return_value = QDialog.Accepted
            dialog.selected_path = Path("processed")
            with patch.object(window, "reload_processed_assets", lambda: setattr(window, "episodes", [episode])):
                window.open_processed_folder()
        self.assertEqual([episode], window.episodes)
        self.assertIn("Loaded 1 of 1 processed asset(s)", window.log.toPlainText())
        window.close()

    def test_loading_legacy_model_values_is_rejected(self):
        from chroma_db_import.representation import QWEN3_PROFILE
        from chroma_db_import.ui_window import MainWindow

        window = MainWindow()
        with self.assertRaises(ValueError):
            window.apply_state_payload(
                {
                    "representation_profile": QWEN3_PROFILE,
                    "embedding_model": "legacy-embedding-model",
                    "embedding_model_revision": "legacy-revision",
                }
            )
        window.close()

    def test_contexts_panel_exposes_live_activity_feedback(self):
        from chroma_db_import.ui_window import MainWindow

        window = MainWindow()
        window.render_contexts()
        self.app.processEvents()
        self.assertIsNotNone(window.progress_label.parentWidget())
        self.assertIsNotNone(window.progress_bar.parentWidget())
        self.assertIsNotNone(window.log.parentWidget())
        window.begin_progress_operation("Checking producer context...")
        self.app.processEvents()
        self.assertIn("Checking producer context", window.progress_label.text())
        self.assertIn("elapsed", window.progress_label.text())
        window.finish_progress_operation("Finished", completed=True)
        self.assertEqual("Finished", window.progress_label.text())
        window.close()

    def test_confirmed_import_is_queued_after_worker_cleanup(self):
        from unittest.mock import patch
        from chroma_db_import.ui_window import MainWindow

        window = MainWindow()
        window.context_import_after_prepare = True
        window.thread = None
        with patch("chroma_db_import.ui_window.QTimer.singleShot") as single_shot:
            window.queue_confirmed_context_import()
        self.assertFalse(window.context_import_after_prepare)
        self.assertEqual(2, single_shot.call_count)
        self.assertEqual(0, single_shot.call_args_list[-1].args[0])
        self.assertTrue(callable(single_shot.call_args_list[-1].args[1]))
        window.close()


if __name__ == "__main__":
    unittest.main()
