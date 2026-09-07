"""
Tests for Worker Loss Recovery, Workspace Invalidation, and Safe Retry.
"""

import unittest
from orchestrator.engine import MissionEngine
from orchestrator.integration.adapter import MockMergeAdapter
from orchestrator.integration.manager import IntegrationManager
from orchestrator.models import EventType, TaskState
from orchestrator.scheduler.aimd import AIMDConfig, AIMDController
from orchestrator.scheduler.scheduler import EventDrivenScheduler
from orchestrator.workers.adapter import MockExecutionAdapter
from orchestrator.workers.affinity import DomainAffinityPolicy
from orchestrator.workers.models import Worker, WorkerState
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workspace.adapter import MockWorktreeAdapter
from orchestrator.workspace.registry import WorkspaceRegistry


class TestWorkerRecovery(unittest.TestCase):
    def setUp(self):
        self.engine = MissionEngine(mission_id="m1", title="Recovery Mission")
        self.worker_registry = WorkerRegistry()
        self.affinity_policy = DomainAffinityPolicy()
        self.aimd_controller = AIMDController(AIMDConfig(min_capacity=1, max_capacity=2, initial_capacity=2))
        self.ready_queue = self.engine.ready_queue
        self.execution_adapter = MockExecutionAdapter()
        self.worktree_adapter = MockWorktreeAdapter()
        self.workspace_registry = WorkspaceRegistry()
        self.merge_adapter = MockMergeAdapter()

        self.integration_manager = IntegrationManager(
            merge_adapter=self.merge_adapter,
            worktree_adapter=self.worktree_adapter,
            workspace_registry=self.workspace_registry,
            event_emitter=self.engine._emit,
        )

        self.scheduler = EventDrivenScheduler(
            engine=self.engine,
            worker_registry=self.worker_registry,
            affinity_policy=self.affinity_policy,
            aimd_controller=self.aimd_controller,
            ready_queue=self.ready_queue,
            execution_adapter=self.execution_adapter,
            workspace_registry=self.workspace_registry,
            worktree_adapter=self.worktree_adapter,
            integration_manager=self.integration_manager,
            event_emitter=self.engine._emit,
        )
        self.engine.attach_scheduler(self.scheduler)

        # Register workers
        w1 = Worker(worker_id="w1", domain="backend")
        w2 = Worker(worker_id="w2", domain="backend")
        self.worker_registry.register_worker(w1)
        self.worker_registry.register_worker(w2)
        self.engine.start_mission()


    def test_worker_crash_releases_workspace_and_retries_task(self):
        self.execution_adapter.auto_complete = False

        task = self.engine.add_task(
            task_id="t1",
            title="Crash Task",
            domain="backend",
            write_set={"src/crash.py"},
            workspace_mode="branch",
        )
        task.max_retries = 2

        # 1. Dispatch task to worker w1
        self.assertEqual(len(self.scheduler.dispatches), 1)
        self.assertEqual(self.scheduler.dispatches[0].worker_id, "w1")
        self.assertTrue(self.workspace_registry.has_active_ownership("t1"))

        # 2. Worker w1 experiences unexpected loss/crash
        self.scheduler.handle_worker_failure(worker_id="w1", error="Process terminated with SIGKILL")

        # 3. Verify w1 is FAILED
        w1 = self.worker_registry.get_worker("w1")
        self.assertEqual(w1.state, WorkerState.FAILED)

        # 4. Retry eligibility: retry count incremented
        self.assertEqual(task.retry_count, 1)

        # 5. Reactive reassignment: task was automatically re-dispatched to idle worker w2
        self.assertEqual(len(self.scheduler.dispatches), 2)
        self.assertEqual(self.scheduler.dispatches[1].worker_id, "w2")
        self.assertEqual(task.status, TaskState.RUNNING)
        self.assertTrue(self.workspace_registry.has_active_ownership("t1"))

        # 6. Complete on retry
        self.scheduler.complete_task("t1")
        self.assertEqual(task.status, TaskState.MERGED)
        self.assertFalse(self.workspace_registry.has_active_ownership("t1"))

    def test_worker_failure_respects_retry_limit(self):
        self.execution_adapter.auto_complete = False

        task = self.engine.add_task(
            task_id="t1",
            title="Exhaust Retries Task",
            domain="backend",
            write_set={"src/crash.py"},
            workspace_mode="branch",
        )
        task.max_retries = 1

        self.assertEqual(task.status, TaskState.RUNNING)
        self.assertEqual(len(self.scheduler.dispatches), 1)

        # First crash -> retries and reactively dispatches to w2
        self.scheduler.handle_worker_failure(worker_id="w1", error="Crash 1")
        self.assertEqual(task.retry_count, 1)
        self.assertEqual(task.status, TaskState.RUNNING)
        self.assertEqual(len(self.scheduler.dispatches), 2)

        # Second crash -> retries exhausted
        self.scheduler.handle_worker_failure(worker_id="w2", error="Crash 2")
        self.assertEqual(task.status, TaskState.FAILED)
        self.assertFalse(self.workspace_registry.has_active_ownership("t1"))



if __name__ == "__main__":
    unittest.main()
