import os
import subprocess
import sys
import unittest
from pathlib import Path


class RedundancyCliTests(unittest.TestCase):
    def test_module_entrypoint_executes_redundancy_parser(self):
        root = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "src")
        result = subprocess.run(
            [sys.executable, "-m", "chroma_db_import.cli", "redundancy", "--help"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode)
        self.assertIn("Assess semantic redundancy", result.stdout)
        self.assertIn("configure-judge", result.stdout)


if __name__ == "__main__":
    unittest.main()
