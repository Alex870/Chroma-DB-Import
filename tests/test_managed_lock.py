import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from chroma_db_import.managed_lock import ManagedPartitionBusy, ManagedPartitionLock


class ManagedPartitionLockTests(unittest.TestCase):
    def test_owner_metadata_is_diagnostic_and_removed_only_by_its_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = ManagedPartitionLock(root, run_id="managed_test_run").acquire()
            metadata = json.loads((root / ".import.lock.owner.json").read_text(encoding="utf-8"))
            self.assertEqual("managed_test_run", metadata["run_id"])
            self.assertEqual(str(root.resolve()), metadata["partition_path"])
            self.assertEqual(lock.path.name, Path(metadata["lock_path"]).name)
            self.assertEqual("active", metadata["status"])
            lock.release()
            self.assertFalse((root / ".import.lock.owner.json").exists())

    def test_release_allows_the_partition_to_be_reacquired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = ManagedPartitionLock(root).acquire()
            first.release()

            second = ManagedPartitionLock(root).acquire()
            second.release()

    def test_context_manager_releases_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "import failed"):
                with ManagedPartitionLock(root):
                    raise RuntimeError("import failed")

            lock = ManagedPartitionLock(root).acquire()
            lock.release()

    def test_contention_is_reported_as_busy_and_does_not_retain_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = ManagedPartitionLock(root).acquire()
            try:
                second = ManagedPartitionLock(root)
                with self.assertRaises(ManagedPartitionBusy):
                    second.acquire()
                self.assertIsNone(second._handle)
            finally:
                first.release()

    def test_setup_failure_closes_partially_opened_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = ManagedPartitionLock(root)
            with patch.object(Path, "stat", side_effect=OSError("lock-file stat failed")):
                with self.assertRaisesRegex(OSError, "lock-file stat failed"):
                    lock.acquire()
            self.assertIsNone(lock._handle)

            reacquired = ManagedPartitionLock(root).acquire()
            reacquired.release()

    def test_release_closes_handle_when_os_unlock_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = ManagedPartitionLock(root).acquire()
            with patch.object(lock, "_unlock", side_effect=OSError("unlock failed")):
                with self.assertRaisesRegex(OSError, "unlock failed"):
                    lock.release()
            self.assertIsNone(lock._handle)

            reacquired = ManagedPartitionLock(root).acquire()
            reacquired.release()


if __name__ == "__main__":
    unittest.main()
