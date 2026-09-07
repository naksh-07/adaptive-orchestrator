"""
Unit Tests for Worker Models, Registry, Domain Affinity, and Execution Adapters.
Adaptive Orchestrator v5 - Phase 2.
"""

import unittest
from orchestrator.exceptions import (
    DuplicateWorkerError,
    InvalidWorkerStateError,
    WorkerNotFoundError,
)
from orchestrator.models import Task, TaskState
from orchestrator.workers.adapter import (
    ExecutionResult,
    LocalExecutionAdapter,
    MockExecutionAdapter,
)
from orchestrator.workers.affinity import DomainAffinityPolicy
from orchestrator.workers.models import (
    Worker,
    WorkerMetrics,
    WorkerState,
)
from orchestrator.workers.registry import WorkerRegistry


class TestWorkerModels(unittest.TestCase):
    def test_worker_initialization(self):
        worker = Worker(worker_id="w1", domain="backend")
        self.assertEqual(worker.worker_id, "w1")
        self.assertEqual(worker.domain, "backend")
        self.assertEqual(worker.state, WorkerState.IDLE)
        self.assertIsNone(worker.current_task_id)
        self.assertEqual(len(worker.task_history), 0)
        self.assertTrue(worker.is_idle)
        self.assertFalse(worker.is_busy)
        self.assertTrue(worker.is_available)

    def test_worker_assign_and_complete_task_reuse(self):
        worker = Worker(worker_id="w1", domain="backend")

        # Assign task 1
        worker.assign_task("t1")
        self.assertEqual(worker.state, WorkerState.BUSY)
        self.assertEqual(worker.current_task_id, "t1")
        self.assertTrue(worker.is_busy)
        self.assertFalse(worker.is_available)

        # Complete task 1
        worker.complete_task("t1", duration=1.5)
        self.assertEqual(worker.state, WorkerState.IDLE)
        self.assertIsNone(worker.current_task_id)
        self.assertEqual(worker.task_history, ["t1"])
        self.assertEqual(worker.metrics.tasks_completed, 1)
        self.assertEqual(worker.metrics.total_execution_time, 1.5)
        self.assertTrue(worker.is_available)

        # Reassign same worker to task 2 (Reuse!)
        worker.assign_task("t2")
        self.assertEqual(worker.state, WorkerState.BUSY)
        self.assertEqual(worker.current_task_id, "t2")

        # Complete task 2
        worker.complete_task("t2", duration=2.0)
        self.assertEqual(worker.state, WorkerState.IDLE)
        self.assertEqual(worker.task_history, ["t1", "t2"])
        self.assertEqual(worker.metrics.tasks_completed, 2)
        self.assertEqual(worker.metrics.total_execution_time, 3.5)

    def test_worker_assign_when_busy_raises_error(self):
        worker = Worker(worker_id="w1")
        worker.assign_task("t1")
        with self.assertRaises(InvalidWorkerStateError):
            worker.assign_task("t2")

    def test_worker_fail_task_returns_to_idle_or_failed(self):
        worker = Worker(worker_id="w1")
        worker.assign_task("t1")

        # Soft task failure (worker itself remains healthy)
        worker.fail_task("t1", error="Assertion failed", duration=0.5, worker_failed=False)
        self.assertEqual(worker.state, WorkerState.IDLE)
        self.assertIsNone(worker.current_task_id)
        self.assertEqual(worker.metrics.tasks_failed, 1)
        self.assertEqual(worker.metrics.consecutive_failures, 1)

        # Fatal worker crash
        worker.assign_task("t2")
        worker.fail_task("t2", error="Process crash", duration=0.2, worker_failed=True)
        self.assertEqual(worker.state, WorkerState.FAILED)
        self.assertFalse(worker.is_available)

        # Recover worker from FAILED to IDLE
        worker.transition_to(WorkerState.IDLE)
        self.assertTrue(worker.is_idle)

    def test_worker_retirement(self):
        worker = Worker(worker_id="w1")
        worker.retire(reason="Mission complete")
        self.assertEqual(worker.state, WorkerState.RETIRED)
        self.assertFalse(worker.is_available)
        self.assertTrue(worker.state.is_terminal)

        # Cannot transition out of RETIRED
        with self.assertRaises(InvalidWorkerStateError):
            worker.transition_to(WorkerState.IDLE)

    def test_worker_to_dict_serialization(self):
        worker = Worker(worker_id="w1", domain="frontend")
        data = worker.to_dict()
        self.assertEqual(data["worker_id"], "w1")
        self.assertEqual(data["domain"], "frontend")
        self.assertEqual(data["state"], "IDLE")
        self.assertEqual(data["metrics"]["tasks_completed"], 0)


class TestWorkerRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = WorkerRegistry()

    def test_register_and_get_worker(self):
        w1 = Worker(worker_id="w1", domain="backend")
        self.registry.register_worker(w1)
        self.assertEqual(self.registry.worker_count, 1)
        self.assertTrue(self.registry.has_worker("w1"))
        self.assertIs(self.registry.get_worker("w1"), w1)

    def test_duplicate_registration_raises_error(self):
        w1 = Worker(worker_id="w1")
        self.registry.register_worker(w1)
        with self.assertRaises(DuplicateWorkerError):
            self.registry.register_worker(Worker(worker_id="w1"))

    def test_unregister_worker(self):
        w1 = Worker(worker_id="w1")
        self.registry.register_worker(w1)
        unregistered = self.registry.unregister_worker("w1")
        self.assertEqual(unregistered.worker_id, "w1")
        self.assertEqual(self.registry.worker_count, 0)
        self.assertFalse(self.registry.has_worker("w1"))

        with self.assertRaises(WorkerNotFoundError):
            self.registry.unregister_worker("w1")

    def test_retire_worker(self):
        w1 = Worker(worker_id="w1")
        self.registry.register_worker(w1)
        retired = self.registry.retire_worker("w1", reason="Context limit")
        self.assertEqual(retired.state, WorkerState.RETIRED)
        self.assertEqual(self.registry.idle_count, 0)
        self.assertTrue(self.registry.has_worker("w1"))

    def test_find_idle_workers_by_domain(self):
        w_be = Worker(worker_id="w_be", domain="backend")
        w_fe = Worker(worker_id="w_fe", domain="frontend")
        w_gen = Worker(worker_id="w_gen", domain="general")

        self.registry.register_worker(w_be)
        self.registry.register_worker(w_fe)
        self.registry.register_worker(w_gen)

        all_idle = self.registry.get_idle_workers()
        self.assertEqual(len(all_idle), 3)

        be_idle = self.registry.get_idle_workers(domain="backend")
        self.assertEqual(len(be_idle), 1)
        self.assertEqual(be_idle[0].worker_id, "w_be")

        # Mark w_be busy
        self.registry.assign_task("w_be", "t1")
        self.assertEqual(self.registry.idle_count, 2)
        self.assertEqual(self.registry.busy_count, 1)
        self.assertEqual(len(self.registry.get_idle_workers(domain="backend")), 0)

    def test_assign_and_release_worker_tracking(self):
        w1 = Worker(worker_id="w1")
        self.registry.register_worker(w1)

        self.registry.assign_task("w1", "t100")
        self.assertEqual(w1.current_task_id, "t100")
        self.assertIs(self.registry.get_worker_for_task("t100"), w1)

        # Release worker after completion
        self.registry.release_worker("w1", success=True, duration=1.0)
        self.assertTrue(w1.is_idle)
        self.assertIsNone(w1.current_task_id)
        self.assertIsNone(self.registry.get_worker_for_task("t100"))
        self.assertEqual(w1.metrics.tasks_completed, 1)

    def test_snapshot_deterministic(self):
        self.registry.register_worker(Worker(worker_id="w_z", domain="general"))
        self.registry.register_worker(Worker(worker_id="w_a", domain="backend"))

        snap = self.registry.snapshot()
        self.assertEqual(len(snap), 2)
        self.assertEqual(snap[0]["worker_id"], "w_a")
        self.assertEqual(snap[1]["worker_id"], "w_z")


class TestDomainAffinityPolicy(unittest.TestCase):
    def setUp(self):
        self.policy = DomainAffinityPolicy(allow_general_fallback=True, allow_cross_domain_fallback=False)

    def test_exact_domain_match_preferred(self):
        task = Task(task_id="t1", mission_id="m1", title="DB Task", domain="database")
        w_db = Worker(worker_id="w_db", domain="database")
        w_gen = Worker(worker_id="w_gen", domain="general")

        selected = self.policy.select_worker(task, [w_gen, w_db])
        self.assertIsNotNone(selected)
        self.assertEqual(selected.worker_id, "w_db")

    def test_general_fallback_when_no_domain_worker(self):
        task = Task(task_id="t1", mission_id="m1", title="UI Task", domain="frontend")
        w_be = Worker(worker_id="w_be", domain="backend")
        w_gen = Worker(worker_id="w_gen", domain="general")

        selected = self.policy.select_worker(task, [w_be, w_gen])
        self.assertIsNotNone(selected)
        self.assertEqual(selected.worker_id, "w_gen")

    def test_no_worker_when_general_fallback_disabled(self):
        strict_policy = DomainAffinityPolicy(allow_general_fallback=False, allow_cross_domain_fallback=False)
        task = Task(task_id="t1", mission_id="m1", title="UI Task", domain="frontend")
        w_be = Worker(worker_id="w_be", domain="backend")
        w_gen = Worker(worker_id="w_gen", domain="general")

        selected = strict_policy.select_worker(task, [w_be, w_gen])
        self.assertIsNone(selected)

    def test_cross_domain_fallback_when_enabled(self):
        flexible_policy = DomainAffinityPolicy(allow_general_fallback=True, allow_cross_domain_fallback=True)
        task = Task(task_id="t1", mission_id="m1", title="UI Task", domain="frontend")
        w_be = Worker(worker_id="w_be", domain="backend")

        selected = flexible_policy.select_worker(task, [w_be])
        self.assertIsNotNone(selected)
        self.assertEqual(selected.worker_id, "w_be")

    def test_deterministic_tie_breaking(self):
        task = Task(task_id="t1", mission_id="m1", title="Task", domain="backend")
        w1 = Worker(worker_id="w1", domain="backend")
        w2 = Worker(worker_id="w2", domain="backend")

        # w1 completed 1 task, w2 completed 0 tasks -> w2 preferred
        w1.metrics.tasks_completed = 1
        w2.metrics.tasks_completed = 0

        selected = self.policy.select_worker(task, [w1, w2])
        self.assertEqual(selected.worker_id, "w2")


class TestExecutionAdapters(unittest.TestCase):
    def test_mock_adapter_distinguishes_spawn_from_reuse(self):
        adapter = MockExecutionAdapter()
        worker = Worker(worker_id="w1", domain="backend")
        task1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        task2 = Task(task_id="t2", mission_id="m1", title="Task 2")

        # First dispatch -> Fresh spawn
        worker.assign_task("t1")
        res1 = adapter.dispatch(worker, task1)
        self.assertTrue(res1.success)
        self.assertFalse(res1.is_reuse)
        self.assertEqual(adapter.spawn_count, 1)
        self.assertEqual(adapter.reuse_count, 0)

        # Worker finishes task 1
        worker.complete_task("t1")

        # Second dispatch -> Wake / Reuse!
        worker.assign_task("t2")
        res2 = adapter.dispatch(worker, task2)
        self.assertTrue(res2.success)
        self.assertTrue(res2.is_reuse)
        self.assertEqual(adapter.spawn_count, 1)
        self.assertEqual(adapter.reuse_count, 1)

    def test_mock_adapter_failure_overrides(self):
        adapter = MockExecutionAdapter()
        adapter.fail_tasks.add("t_fail")

        worker = Worker(worker_id="w1")
        task_ok = Task(task_id="t_ok", mission_id="m1", title="OK")
        task_fail = Task(task_id="t_fail", mission_id="m1", title="Fail")

        self.assertTrue(adapter.dispatch(worker, task_ok).success)
        self.assertFalse(adapter.dispatch(worker, task_fail).success)

    def test_local_execution_adapter(self):
        def handler(w: Worker, t: Task) -> ExecutionResult:
            return ExecutionResult(success=True, result={"processed_by": w.worker_id})

        adapter = LocalExecutionAdapter(handler=handler)
        worker = Worker(worker_id="w1")
        task = Task(task_id="t1", mission_id="m1", title="Task")

        res = adapter.dispatch(worker, task)
        self.assertTrue(res.success)
        self.assertEqual(res.result["processed_by"], "w1")


if __name__ == "__main__":
    unittest.main()
