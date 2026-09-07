"""
Tests for MergeQueue, MockMergeAdapter, and Sequential Integration.
"""

import unittest
from orchestrator.exceptions import MergeConflictError
from orchestrator.integration.adapter import MockMergeAdapter
from orchestrator.integration.models import MergeRequest, MergeResult, MergeStatus
from orchestrator.integration.queue import MergeQueue
from orchestrator.models import Event, EventType, Task, TaskState
from orchestrator.workspace.adapter import MockWorktreeAdapter
from orchestrator.workspace.registry import WorkspaceRegistry


class TestMergeQueue(unittest.TestCase):
    def setUp(self):
        self.merge_adapter = MockMergeAdapter()
        self.worktree_adapter = MockWorktreeAdapter()
        self.workspace_registry = WorkspaceRegistry()
        self.events = []

        def emitter(event_type, task_id="", payload=None):
            self.events.append((event_type, task_id, payload or {}))

        self.queue = MergeQueue(
            merge_adapter=self.merge_adapter,
            worktree_adapter=self.worktree_adapter,
            workspace_registry=self.workspace_registry,
            event_emitter=emitter,
            target_branch="main",
        )

    def test_deterministic_fifo_ordering(self):
        t1 = Task(task_id="t1", title="T1", priority=1)
        t2 = Task(task_id="t2", title="T2", priority=10)
        t3 = Task(task_id="t3", title="T3", priority=5)

        r1 = self.queue.submit(t1, "w1", "branch-1", "/ws/1")
        r2 = self.queue.submit(t2, "w2", "branch-2", "/ws/2")
        r3 = self.queue.submit(t3, "w3", "branch-3", "/ws/3")

        # By default, ordered by enqueued_at FIFO
        pending = self.queue.get_pending_requests()
        self.assertEqual([r.task_id for r in pending], ["t1", "t2", "t3"])

    def test_single_active_merge_at_a_time(self):
        t1 = Task(task_id="t1", title="T1")
        t2 = Task(task_id="t2", title="T2")

        # Acquire in registry and mock worktrees
        self.workspace_registry.acquire(t1, "w1", workspace_path="/ws/1", branch_name="b1")
        self.workspace_registry.acquire(t2, "w2", workspace_path="/ws/2", branch_name="b2")
        self.worktree_adapter.create_workspace("t1", "b1")
        self.worktree_adapter.create_workspace("t2", "b2")

        self.queue.submit(t1, "w1", "b1", "/ws/1")
        self.queue.submit(t2, "w2", "b2", "/ws/2")

        # Process one merge
        result1 = self.queue.process_next()
        self.assertIsNotNone(result1)
        self.assertEqual(result1.task_id, "t1")
        self.assertEqual(result1.status, MergeStatus.SUCCESS)

        # After processing t1, queue is empty of active merges, so t2 is next
        self.assertIsNone(self.queue.active_merge)
        result2 = self.queue.process_next()
        self.assertIsNotNone(result2)
        self.assertEqual(result2.task_id, "t2")
        self.assertEqual(result2.status, MergeStatus.SUCCESS)

    def test_successful_merge_cleans_workspace_and_releases_ownership(self):
        t1 = Task(task_id="t1", title="T1", status=TaskState.READY)
        t1.transition_to(TaskState.RUNNING)
        t1.transition_to(TaskState.PASSED)


        ws_path = self.worktree_adapter.create_workspace("t1", "b1")
        self.workspace_registry.acquire(t1, "w1", workspace_path=ws_path, branch_name="b1")

        self.queue.submit(t1, "w1", "b1", ws_path)
        result = self.queue.process_next()

        self.assertEqual(result.status, MergeStatus.SUCCESS)
        # Task transitioned to MERGED
        self.assertEqual(t1.status, TaskState.MERGED)
        # Worktree cleaned
        self.assertFalse(self.worktree_adapter.has_workspace(ws_path))
        # Logical ownership released
        self.assertFalse(self.workspace_registry.has_active_ownership("t1"))

        # Event emitted
        event_types = [e[0] for e in self.events]
        self.assertIn(EventType.MERGE_READY, event_types)
        self.assertIn(EventType.MERGE_STARTED, event_types)
        self.assertIn(EventType.MERGE_COMPLETED, event_types)
        self.assertIn(EventType.WORKSPACE_RELEASED, event_types)

    def test_merge_conflict_retains_evidence(self):
        t1 = Task(task_id="t1", title="T1", status=TaskState.READY)
        t1.transition_to(TaskState.RUNNING)
        t1.transition_to(TaskState.PASSED)


        ws_path = self.worktree_adapter.create_workspace("t1", "b1")
        self.workspace_registry.acquire(t1, "w1", workspace_path=ws_path, branch_name="b1")

        # Simulate merge conflict in adapter
        self.merge_adapter.fail_merge("b1", conflict_files=["src/core.py"])

        self.queue.submit(t1, "w1", "b1", ws_path)
        result = self.queue.process_next()

        self.assertEqual(result.status, MergeStatus.CONFLICT)
        self.assertEqual(result.conflict_files, ["src/core.py"])

        # Workspace ownership logically released as failed so future work is not blocked
        self.assertFalse(self.workspace_registry.has_active_ownership("t1"))
        # But worktree is RETAINED for evidence/debugging!
        self.assertTrue(self.worktree_adapter.has_workspace(ws_path))

        # Event emitted
        event_types = [e[0] for e in self.events]
        self.assertIn(EventType.MERGE_FAILED, event_types)


if __name__ == "__main__":
    unittest.main()
