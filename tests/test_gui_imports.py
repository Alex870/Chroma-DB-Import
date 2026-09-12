from __future__ import annotations

import subprocess
import sys
import unittest


class GuiImportTests(unittest.TestCase):
    def test_ui_export_import_does_not_trigger_workflow_service_cycle(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from chroma_db_import.ui_export import DocumentLike; print(DocumentLike.__name__)",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "DocumentLike")


if __name__ == "__main__":
    unittest.main()
