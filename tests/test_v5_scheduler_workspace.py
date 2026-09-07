"""
Tests for Scheduler Controlled Workspace Acquisition and Concurrency.
"""

import unittest
from orchestrator.engine import MissionEngine
from orchestrator.integration.adapter import MockMergeAdapter
from orchestrator.integration.manager import IntegrationManager
from orchestrator.models import EventType, TaskState
from orchestrator.scheduler.aimd import AIMDConfig, AIMDController
from orchestrator.scheduler.ready_queue import ReadyQueue
from orchestrator.scheduler.scheduler import EventDrivenScheduler
from orchestrator.workers.adapter import MockExecutionAdapter
from orchestrator.workers.affinity import DomainAffinityPolicy
from orchestrator.workers.models import Worker, WorkerState
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workspace.adapter import MockWorktreeAdapter
from orchestrator.workspace.registry import WorkspaceRegistry


class TestSchedulerWorkspace(unittest.TestCase):
    def setUp(self):
        self.engine = MissionEngine(mission_id="m1", title="Workspace Mission")
        self.worker_registry = WorkerRegistry()
        self.affinity_policy = DomainAffinityPolicy()
        self.aimd_controller = AIMDController(AIMDConfig(min_capacity=2, max_capacity=4, initial_capacity=2))
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


    def test_disjoint_tasks_execute_concurrently(self):
        self.execution_adapter.auto_complete = False  # Async mode

        # Two tasks with completely disjoint write sets
        t1 = self.engine.add_task(
            task_id="t1",
            title="Task 1",
            domain="backend",
            write_set={"src/service_a.py"},
            workspace_mode="branch",
        )
        t2 = self.engine.add_task(
            task_id="t2",
            title="Task 2",
            domain="backend",
            write_set={"src/service_b.py"},
            workspace_mode="branch",
        )

        # Reactive engine dispatches upon TASK_READY
        self.assertEqual(len(self.scheduler.dispatches), 2)
        self.assertEqual(t1.status, TaskState.RUNNING)
        self.assertEqual(t2.status, TaskState.RUNNING)

        # Both have active workspace ownership
        self.assertTrue(self.workspace_registry.has_active_ownership("t1"))
        self.assertTrue(self.workspace_registry.has_active_ownership("t2"))

    def test_conflicting_task_waits_in_ready_queue(self):
        self.execution_adapter.auto_complete = False

        # Two tasks that want to write to the same file
        t1 = self.engine.add_task(
            task_id="t1",
            title="Task 1",
            domain="backend",
            priority=10,
            write_set={"src/config.py"},
            workspace_mode="branch",
        )
        t2 = self.engine.add_task(
            task_id="t2",
            title="Task 2",
            domain="backend",
            priority=5,
            write_set={"src/config.py"},
            workspace_mode="branch",
        )

        # t1 (higher priority) dispatched; t2 holds due to write collision with t1
        self.assertEqual(len(self.scheduler.dispatches), 1)
        self.assertEqual(self.scheduler.dispatches[0].task_id, "t1")
        self.assertEqual(t1.status, TaskState.RUNNING)
        self.assertEqual(t2.status, TaskState.READY)  # Waiting, NOT failed!

        # Check AIMD: capacity must NOT decrease due to workspace conflict
        initial_cap = self.aimd_controller.current_capacity
        self.assertEqual(initial_cap, 2)

        # Now complete t1
        self.scheduler.complete_task("t1")
        self.assertEqual(t1.status, TaskState.MERGED)
        self.assertFalse(self.workspace_registry.has_active_ownership("t1"))

        # Releasing t1's workspace reactively unblocks and dispatches t2
        self.assertEqual(t2.status, TaskState.RUNNING)
        self.assertTrue(self.workspace_registry.has_active_ownership("t2"))
        self.assertEqual(len(self.scheduler.dispatches), 2)


    def test_worker_reusable_after_workspace_release(self):
        engine = MissionEngine(mission_id="m_reuse", title="Reuse Mission")
        worker_registry = WorkerRegistry()
        w1 = Worker(worker_id="w1", domain="backend")
        worker_registry.register_worker(w1)

        ws_reg = WorkspaceRegistry()
        int_mgr = IntegrationManager(
            merge_adapter=self.merge_adapter,
            worktree_adapter=self.worktree_adapter,
            workspace_registry=ws_reg,
            event_emitter=engine._emit,
        )

        scheduler = EventDrivenScheduler(
            engine=engine,
            worker_registry=worker_registry,
            affinity_policy=self.affinity_policy,
            aimd_controller=self.aimd_controller,
            ready_queue=engine.ready_queue,
            execution_adapter=self.execution_adapter,
            workspace_registry=ws_reg,
            worktree_adapter=self.worktree_adapter,
            integration_manager=int_mgr,
            event_emitter=engine._emit,
        )
        engine.attach_scheduler(scheduler)

        # Add both tasks upfront where t2 depends on t1
        engine.add_task(
            task_id="t1",
            title="Task 1",
            domain="backend",
            write_set={"src/a.py"},
            workspace_mode="branch",
        )
        engine.add_task(
            task_id="t2",
            title="Task 2",
            domain="backend",
            dependencies=["t1"],
            write_set={"src/b.py"},
            workspace_mode="branch",
        )

        self.execution_adapter.auto_complete = True  # Sync mode
        engine.start_mission()

        # Both tasks executed sequentially on worker w1
        t1 = engine.graph.get_task("t1")
        t2 = engine.graph.get_task("t2")
        self.assertEqual(t1.status, TaskState.MERGED)
        self.assertEqual(t2.status, TaskState.MERGED)
        self.assertEqual(w1.state, WorkerState.IDLE)
        self.assertEqual(w1.metrics.tasks_completed, 2)


if __name__ == "__main__":
    unittest.main()
