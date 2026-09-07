"""
Tests for Task and Mission Cancellation Workspace Cleanup.
"""

import unittest
from orchestrator.engine import MissionEngine
from orchestrator.models import TaskState
from orchestrator.workspace.adapter import MockWorktreeAdapter
from orchestrator.workspace.models import WorkspaceMode, WorkspaceReleaseState
from orchestrator.workspace.registry import WorkspaceRegistry


class TestCancellationWorkspace(unittest.TestCase):
    def setUp(self):
        self.worktree_adapter = MockWorktreeAdapter()
        self.workspace_registry = WorkspaceRegistry()
        self.engine = MissionEngine(
            mission_id="m1",
            title="Cancellation Mission",
            workspace_registry=self.workspace_registry,
            worktree_adapter=self.worktree_adapter,
        )

    def test_cancellation_releases_workspace_and_cleans_worktree(self):
        t1 = self.engine.add_task(
            task_id="t1",
            title="Task 1",
            write_set={"src/cancelled.py"},
            workspace_mode="branch",
        )
        t2 = self.engine.add_task(
            task_id="t2",
            title="Task 2",
            dependencies=["t1"],
            write_set={"src/downstream.py"},
            workspace_mode="branch",
        )

        # Simulate task starting and acquiring workspace
        ws_path = self.worktree_adapter.create_workspace("t1", "branch-t1")
        self.workspace_registry.acquire(
            task=t1,
            worker_id="w1",
            workspace_mode=WorkspaceMode.BRANCH,
            workspace_path=ws_path,
            branch_name="branch-t1",
        )
        t1.workspace_path = ws_path
        t1.branch_name = "branch-t1"
        self.engine.mark_task_started("t1")

        self.assertTrue(self.workspace_registry.has_active_ownership("t1"))
        self.assertTrue(self.worktree_adapter.has_workspace(ws_path))

        # Cancel t1 (should cascade to t2)
        cancelled = self.engine.mark_task_cancelled("t1", reason="User aborted")
        self.assertEqual(len(cancelled), 2)
        self.assertEqual(t1.status, TaskState.CANCELLED)
        self.assertEqual(t2.status, TaskState.CANCELLED)

        # Workspace ownership must be cleanly released (no stale locks)
        self.assertFalse(self.workspace_registry.has_active_ownership("t1"))
        record = self.workspace_registry.get_record("t1")
        self.assertEqual(record.release_state, WorkspaceReleaseState.CANCELLED)

        # Physical worktree must be cleaned up
        self.assertFalse(self.worktree_adapter.has_workspace(ws_path))

        # Neither task should be ready or in queue
        self.assertTrue(self.engine.ready_queue.is_empty())


if __name__ == "__main__":
    unittest.main()
