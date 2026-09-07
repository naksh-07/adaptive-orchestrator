"""
Unit and Integration Tests for Event-Driven Scheduler.
Adaptive Orchestrator v5 - Phase 2.
"""

import unittest
from orchestrator.engine import MissionEngine
from orchestrator.models import EventType, MissionState, Task, TaskState
from orchestrator.scheduler.ready_queue import ReadyQueue
from orchestrator.scheduler.scheduler import EventDrivenScheduler
from orchestrator.workers.adapter import ExecutionResult, MockExecutionAdapter
from orchestrator.workers.affinity import DomainAffinityPolicy
from orchestrator.workers.models import Worker, WorkerState
from orchestrator.workers.registry import WorkerRegistry


class TestEventDrivenScheduler(unittest.TestCase):
    def setUp(self):
        self.ready_queue = ReadyQueue()
        self.worker_registry = WorkerRegistry()
        self.adapter = MockExecutionAdapter(default_success=True)
        self.scheduler = EventDrivenScheduler(
            ready_queue=self.ready_queue,
            worker_registry=self.worker_registry,
            execution_adapter=self.adapter,
        )

    def test_scheduler_no_op_when_queue_or_workers_empty(self):
        # Empty queue and empty workers
        dispatches = self.scheduler.evaluate()
        self.assertEqual(len(dispatches), 0)

        # Workers present but queue empty
        self.worker_registry.register_worker(Worker(worker_id="w1"))
        dispatches = self.scheduler.evaluate()
        self.assertEqual(len(dispatches), 0)

        # Queue has tasks but no workers registered
        self.worker_registry.unregister_worker("w1")
        t1 = Task(task_id="t1", mission_id="m1", title="T1", status=TaskState.READY)
        self.ready_queue.push(t1)
        dispatches = self.scheduler.evaluate()
        self.assertEqual(len(dispatches), 0)
        self.assertEqual(len(self.ready_queue), 1)

    def test_single_task_dispatch_and_worker_assignment(self):
        w1 = Worker(worker_id="w1", domain="backend")
        self.worker_registry.register_worker(w1)

        t1 = Task(task_id="t1", mission_id="m1", title="Backend Task", domain="backend", status=TaskState.READY)
        self.ready_queue.push(t1)

        # Evaluate scheduler
        dispatches = self.scheduler.evaluate()
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(dispatches[0].task_id, "t1")
        self.assertEqual(dispatches[0].worker_id, "w1")
        self.assertFalse(dispatches[0].is_reuse)

        # Ready queue should now be empty
        self.assertTrue(self.ready_queue.is_empty())

        # Worker should be reused and now back to IDLE (because MockExecutionAdapter completed synchronously)
        self.assertTrue(w1.is_idle)
        self.assertEqual(w1.metrics.tasks_completed, 1)
        self.assertEqual(w1.task_history, ["t1"])

    def test_dispatch_multiple_independent_tasks_concurrently(self):
        # 3 idle workers
        w1 = Worker(worker_id="w1", domain="backend")
        w2 = Worker(worker_id="w2", domain="frontend")
        w3 = Worker(worker_id="w3", domain="general")
        self.worker_registry.register_worker(w1)
        self.worker_registry.register_worker(w2)
        self.worker_registry.register_worker(w3)

        # 3 independent ready tasks
        t1 = Task(task_id="t1", mission_id="m1", title="T1", domain="backend", status=TaskState.READY)
        t2 = Task(task_id="t2", mission_id="m1", title="T2", domain="frontend", status=TaskState.READY)
        t3 = Task(task_id="t3", mission_id="m1", title="T3", domain="general", status=TaskState.READY)

        self.ready_queue.push(t1)
        self.ready_queue.push(t2)
        self.ready_queue.push(t3)

        dispatches = self.scheduler.evaluate()
        self.assertEqual(len(dispatches), 3)

        dispatched_tasks = {d.task_id for d in dispatches}
        self.assertEqual(dispatched_tasks, {"t1", "t2", "t3"})
        self.assertTrue(self.ready_queue.is_empty())

    def test_worker_reuse_across_sequential_tasks(self):
        # Single worker
        w1 = Worker(worker_id="w1", domain="backend")
        self.worker_registry.register_worker(w1)

        t1 = Task(task_id="t1", mission_id="m1", title="T1", domain="backend", status=TaskState.READY)
        t2 = Task(task_id="t2", mission_id="m1", title="T2", domain="backend", status=TaskState.READY)

        self.ready_queue.push(t1)
        self.ready_queue.push(t2)

        # Evaluate: t1 dispatches to w1, completes synchronously, w1 returns to IDLE,
        # continuous loop immediately evaluates t2 and dispatches to w1!
        dispatches = self.scheduler.evaluate()
        self.assertEqual(len(dispatches), 2)
        self.assertEqual(dispatches[0].task_id, "t1")
        self.assertEqual(dispatches[0].worker_id, "w1")
        self.assertFalse(dispatches[0].is_reuse)  # Fresh spawn

        self.assertEqual(dispatches[1].task_id, "t2")
        self.assertEqual(dispatches[1].worker_id, "w1")
        self.assertTrue(dispatches[1].is_reuse)  # Reused!

        # Invariant: w1 executed both tasks without destroying/recreating worker object
        self.assertEqual(w1.metrics.tasks_completed, 2)
        self.assertEqual(w1.task_history, ["t1", "t2"])
        self.assertEqual(self.adapter.reuse_count, 1)
        self.assertEqual(self.adapter.spawn_count, 1)

    def test_scheduler_no_duplicate_task_dispatch(self):
        w1 = Worker(worker_id="w1")
        self.worker_registry.register_worker(w1)

        t1 = Task(task_id="t1", mission_id="m1", title="T1", status=TaskState.READY)
        self.ready_queue.push(t1)

        dispatches1 = self.scheduler.evaluate()
        self.assertEqual(len(dispatches1), 1)

        # Second evaluation should find no ready tasks
        dispatches2 = self.scheduler.evaluate()
        self.assertEqual(len(dispatches2), 0)

    def test_scheduler_handles_task_failure(self):
        self.adapter.fail_tasks.add("t_bad")
        w1 = Worker(worker_id="w1")
        self.worker_registry.register_worker(w1)

        t_bad = Task(task_id="t_bad", mission_id="m1", title="Bad Task", status=TaskState.READY)
        self.ready_queue.push(t_bad)

        dispatches = self.scheduler.evaluate()
        self.assertEqual(len(dispatches), 1)

        # Worker handled failure and returned to IDLE (soft failure)
        self.assertTrue(w1.is_idle)
        self.assertEqual(w1.metrics.tasks_failed, 1)
        self.assertEqual(w1.metrics.tasks_completed, 0)


class TestMissionEngineWithScheduler(unittest.TestCase):
    def test_engine_event_driven_dispatch_and_dependency_unlock(self):
        """
        Tests continuous event-driven pipeline:
        Engine has T1 (root) -> T2 (depends on T1).
        When T1 completes, T2 automatically becomes READY, triggering the scheduler
        to dispatch T2 to the worker immediately without wave barriers.
        """
        engine = MissionEngine(mission_id="m1", title="Continuous Mission")

        adapter = MockExecutionAdapter(default_success=True)
        scheduler = EventDrivenScheduler(
            ready_queue=engine.ready_queue,
            worker_registry=engine.workers,
            execution_adapter=adapter,
        )
        engine.attach_scheduler(scheduler)

        # Register reusable worker
        worker = Worker(worker_id="w_core", domain="general")
        engine.register_worker(worker)

        # Add DAG: t1 -> t2
        engine.add_task(task_id="t1", title="Task 1")
        engine.add_task(task_id="t2", title="Task 2", dependencies=["t1"])

        # Start mission
        engine.start_mission()

        # Both tasks should have executed continuously!
        self.assertEqual(engine.graph.get_task("t1").status, TaskState.PASSED)
        self.assertEqual(engine.graph.get_task("t2").status, TaskState.PASSED)
        self.assertEqual(engine.mission.state, MissionState.COMPLETED)

        # Verify worker reuse
        self.assertEqual(worker.metrics.tasks_completed, 2)
        self.assertEqual(worker.task_history, ["t1", "t2"])
        self.assertEqual(adapter.reuse_count, 1)
        self.assertEqual(adapter.spawn_count, 1)

        # Verify event stream
        events = engine.get_events()
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.WORKER_REGISTERED, event_types)
        self.assertIn(EventType.TASK_ASSIGNED, event_types)
        self.assertIn(EventType.TASK_STARTED, event_types)
        self.assertIn(EventType.TASK_COMPLETED, event_types)
        self.assertIn(EventType.WORKER_IDLE, event_types)

    def test_engine_with_multiple_workers_and_independent_branches(self):
        """
        Tests diamond graph:
               t_root
              /      \
            t_be     t_fe
              \      /
               t_sink
        With 2 workers (backend, frontend).
        t_be and t_fe execute concurrently on their respective domain workers.
        """
        engine = MissionEngine(mission_id="m_diamond", title="Diamond Graph")

        adapter = MockExecutionAdapter(default_success=True)
        scheduler = EventDrivenScheduler(
            ready_queue=engine.ready_queue,
            worker_registry=engine.workers,
            execution_adapter=adapter,
        )
        engine.attach_scheduler(scheduler)

        # Register 2 specialized workers
        w_be = Worker(worker_id="w_be", domain="backend")
        w_fe = Worker(worker_id="w_fe", domain="frontend")
        engine.register_worker(w_be)
        engine.register_worker(w_fe)

        engine.add_task(task_id="t_root", title="Root", domain="backend")
        engine.add_task(task_id="t_be", title="Backend Task", domain="backend", dependencies=["t_root"])
        engine.add_task(task_id="t_fe", title="Frontend Task", domain="frontend", dependencies=["t_root"])
        engine.add_task(task_id="t_sink", title="Sink Task", domain="backend", dependencies=["t_be", "t_fe"])

        engine.start_mission()

        # All tasks completed
        self.assertEqual(engine.mission.state, MissionState.COMPLETED)
        self.assertTrue(all(t.status == TaskState.PASSED for t in engine.graph.all_tasks()))

        # Verify affinity: t_fe executed on w_fe
        fe_dispatches = [d for d in scheduler.dispatches if d.task_id == "t_fe"]
        self.assertEqual(len(fe_dispatches), 1)
        self.assertEqual(fe_dispatches[0].worker_id, "w_fe")

        # Verify worker reuse on backend worker
        self.assertGreaterEqual(w_be.metrics.tasks_completed, 2)


if __name__ == "__main__":
    unittest.main()
