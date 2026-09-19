import tempfile
import unittest
from pathlib import Path

from chroma_db_import.lock_repair import LockOwner, repair_partition_lock
from chroma_db_import.managed_lock import ManagedPartitionLock


class FakeController:
    def __init__(self, lock: ManagedPartitionLock, *, release_on_graceful: bool = False) -> None:
        self.lock = lock
        self.release_on_graceful = release_on_graceful
        self.alive = True
        self.calls: list[tuple[str, int]] = []

    def is_alive(self, pid: int) -> bool:
        return self.alive

    def request_graceful_stop(self, pid: int) -> bool:
        self.calls.append(("graceful", pid))
        if self.release_on_graceful:
            self.lock.release()
            self.alive = False
        return True

    def force_terminate(self, pid: int) -> bool:
        self.calls.append(("force", pid))
        self.lock.release()
        self.alive = False
        return True


def importer_owner(pid: int = 8123, *, status: str = "untracked") -> LockOwner:
    return LockOwner(
        pid=pid,
        name="python.exe",
        executable=r"D:\Pod Cast RAG\Chroma DB Import\.venv\Scripts\python.exe",
        command_line="python -m chroma_db_import.cli contexts import",
        process_start_time=1.0,
        importer_like=True,
        target_verified=True,
        status=status,
    )


class LockRepairTests(unittest.TestCase):
    def test_already_available_preserves_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = repair_partition_lock(root)
            self.assertEqual(result["status"], "already_available")
            self.assertTrue((root / ".import.lock").is_file())

    def test_ambiguous_owner_is_blocked_without_termination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            held = ManagedPartitionLock(root).acquire()
            controller = FakeController(held)
            try:
                result = repair_partition_lock(
                    root,
                    confirm=True,
                    owner_resolver=lambda *_: [importer_owner(1), importer_owner(2)],
                    process_controller=controller,
                )
            finally:
                held.release()
            self.assertEqual(result["status"], "blocked_active_owner")
            self.assertEqual(controller.calls, [])

    def test_active_owner_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            held = ManagedPartitionLock(root).acquire()
            controller = FakeController(held)
            try:
                result = repair_partition_lock(
                    root,
                    confirm=True,
                    owner_resolver=lambda *_: [importer_owner(status="active")],
                    process_controller=controller,
                )
            finally:
                held.release()
            self.assertEqual(result["status"], "blocked_active_owner")
            self.assertEqual(controller.calls, [])

    def test_graceful_stop_precedes_force_and_only_exact_pid_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            held = ManagedPartitionLock(root).acquire()
            controller = FakeController(held)
            try:
                result = repair_partition_lock(
                    root,
                    confirm=True,
                    grace_timeout=0,
                    owner_resolver=lambda *_: [importer_owner(8123)],
                    process_controller=controller,
                )
            finally:
                if held._handle is not None:
                    held.release()
            self.assertEqual(result["status"], "owner_terminated")
            self.assertEqual(controller.calls, [("graceful", 8123), ("force", 8123)])
            self.assertTrue(result["lock_file_preserved"])

    def test_graceful_release_does_not_force_terminate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            held = ManagedPartitionLock(root).acquire()
            controller = FakeController(held, release_on_graceful=True)
            result = repair_partition_lock(
                root,
                confirm=True,
                owner_resolver=lambda *_: [importer_owner()],
                process_controller=controller,
            )
            self.assertEqual(result["status"], "released")
            self.assertEqual(controller.calls, [("graceful", 8123)])
            self.assertTrue((root / ".import.lock").is_file())

    def test_unverified_owner_is_never_terminated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            held = ManagedPartitionLock(root).acquire()
            controller = FakeController(held)
            try:
                result = repair_partition_lock(
                    root,
                    confirm=True,
                    owner_resolver=lambda *_: [LockOwner(pid=7, importer_like=False, target_verified=True)],
                    process_controller=controller,
                )
            finally:
                held.release()
            self.assertEqual(result["status"], "blocked_active_owner")
            self.assertEqual(controller.calls, [])


if __name__ == "__main__":
    unittest.main()
