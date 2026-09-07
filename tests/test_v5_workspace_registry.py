"""
Tests for WorkspaceRegistry: acquisition, release, duplicate prevention, and snapshotting.
"""

import unittest
from orchestrator.exceptions import (
    WorkspaceAcquisitionError,
    WorkspaceConflictError,
    WorkspaceNotFoundError,
)
from orchestrator.models import Task
from orchestrator.workspace.models import (
    WorkspaceMode,
    WorkspaceRecord,
    WorkspaceReleaseState,
)
from orchestrator.workspace.registry import WorkspaceRegistry


class TestWorkspaceRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = WorkspaceRegistry()

    def test_acquire_and_release(self):
        task = Task(
            task_id="t1",
            title="Task 1",
            write_set={"src/api.py"},
            workspace_mode="branch",
        )
        record = self.registry.acquire(
            task=task,
            worker_id="w1",
            workspace_mode=WorkspaceMode.BRANCH,
            workspace_path="/tmp/worktree/t1",
            branch_name="agent/t1-branch",
        )

        self.assertIsInstance(record, WorkspaceRecord)
        self.assertEqual(record.task_id, "t1")
        self.assertEqual(record.worker_id, "w1")
        self.assertEqual(record.release_state, WorkspaceReleaseState.ACTIVE)
        self.assertTrue(self.registry.has_active_ownership("t1"))

        # Release
        released = self.registry.release("t1", state=WorkspaceReleaseState.RELEASED)
        self.assertEqual(released.release_state, WorkspaceReleaseState.RELEASED)
        self.assertFalse(self.registry.has_active_ownership("t1"))

    def test_duplicate_acquisition_prevention(self):
        task = Task(task_id="t1", title="Task 1", write_set={"src/api.py"})
        self.registry.acquire(
            task=task,
            worker_id="w1",
            workspace_mode=WorkspaceMode.BRANCH,
        )

        # Attempt to acquire again for same task
        with self.assertRaises(WorkspaceAcquisitionError):
            self.registry.acquire(
                task=task,
                worker_id="w2",
                workspace_mode=WorkspaceMode.BRANCH,
            )

    def test_conflicting_write_set_acquisition_fails(self):
        t1 = Task(task_id="t1", title="T1", write_set={"src/service.py"})
        t2 = Task(task_id="t2", title="T2", write_set={"src/service.py"})

        self.registry.acquire(task=t1, worker_id="w1")

        with self.assertRaises(WorkspaceConflictError):
            self.registry.acquire(task=t2, worker_id="w2")

    def test_disjoint_write_set_acquisition_succeeds(self):
        t1 = Task(task_id="t1", title="T1", write_set={"src/service_a.py"})
        t2 = Task(task_id="t2", title="T2", write_set={"src/service_b.py"})

        r1 = self.registry.acquire(task=t1, worker_id="w1")
        r2 = self.registry.acquire(task=t2, worker_id="w2")

        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)
        self.assertEqual(len(self.registry.get_active_records()), 2)

    def test_release_non_existent_fails(self):
        with self.assertRaises(WorkspaceNotFoundError):
            self.registry.release("non_existent_task")

    def test_release_for_worker(self):
        t1 = Task(task_id="t1", title="T1", write_set={"src/a.py"})
        self.registry.acquire(task=t1, worker_id="w1")

        released_records = self.registry.release_for_worker(
            worker_id="w1",
            state=WorkspaceReleaseState.FAILED,
            reason="Worker died",
        )
        self.assertEqual(len(released_records), 1)
        self.assertEqual(released_records[0].task_id, "t1")
        self.assertEqual(released_records[0].release_state, WorkspaceReleaseState.FAILED)
        self.assertFalse(self.registry.has_active_ownership("t1"))

    def test_stale_cleanup(self):
        t1 = Task(task_id="t1", title="T1", write_set={"src/a.py"})
        rec = self.registry.acquire(task=t1, worker_id="w1")
        # Artificially modify acquisition timestamp
        rec.acquired_at -= 5000.0

        cleaned = self.registry.cleanup_stale_ownership(max_age_seconds=3600.0)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].task_id, "t1")
        self.assertEqual(cleaned[0].release_state, WorkspaceReleaseState.INVALIDATED)
        self.assertFalse(self.registry.has_active_ownership("t1"))

    def test_snapshot(self):
        t1 = Task(task_id="t1", title="T1", write_set={"src/a.py"})
        self.registry.acquire(task=t1, worker_id="w1", workspace_path="/path/1", branch_name="b1")

        snap = self.registry.snapshot()
        self.assertEqual(snap["total_records"], 1)
        self.assertEqual(snap["active_records"], 1)
        self.assertIn("t1", snap["records"])
        self.assertEqual(snap["records"]["t1"]["worker_id"], "w1")


if __name__ == "__main__":
    unittest.main()
