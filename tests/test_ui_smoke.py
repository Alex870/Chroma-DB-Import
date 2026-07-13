import os
import unittest
from importlib.util import find_spec


@unittest.skipUnless(find_spec("PySide6"), "PySide6 is not installed in this environment")
class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_constructs_with_frontier_controls(self):
        from chroma_db_import.ui_window import MainWindow
        window = MainWindow()
        self.assertEqual(window.mirror_removals.text(), "Mirror records removed from source caches")
        self.assertEqual(window.experimental_bge_m3.text(), "Use pinned BGE-M3 dense shadow profile")
        self.assertIn("minimal", [window.contextualization.itemText(index) for index in range(window.contextualization.count())])
        window.close()


if __name__ == "__main__":
    unittest.main()
