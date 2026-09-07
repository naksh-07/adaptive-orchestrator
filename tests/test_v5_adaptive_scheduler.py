"""
Unit and Integration tests for Adaptive Concurrency & Routing in EventDrivenScheduler.
Adaptive Orchestrator v5 - Phase 3.
"""

import unittest
from orchestrator.engine import MissionEngine
from orchestrator.models import Event, EventType, MissionState, Task, TaskState
from orchestrator.routing.models import ModelTier, RouterConfig
from orchestrator.routing.router import ModelRouter
from orchestrator.scheduler.aimd import AIMDConfig, AIMDController
from orchestrator.scheduler.ready_queue import ReadyQueue
from orchestrator.scheduler.scheduler import EventDrivenScheduler
from orchestrator.workers.adapter import ExecutionResult, MockExecutionAdapter
from orchestrator.workers.models import Worker
from orchestrator.workers.registry import WorkerRegistry


class TestAdaptiveScheduler(unittest.TestCase):
    def setUp(self):
        self.ready_queue = ReadyQueue()
        self.worker_registry = WorkerRegistry()
        self.adapter = MockExecutionAdapter(default_success=True)
        # AIMD with capacity = 2, min = 2, max = 5
        self.aimd = AIMDController(AIMDConfig(min_capacity=2, max_capacity=5, initial_capacity=2))
        self.router = ModelRouter(RouterConfig(high_priority_threshold=8.0))
        self.events = []

        self.scheduler = EventDrivenScheduler(
            ready_queue=self.ready_queue,
            worker_registry=self.worker_registry,
            execution_adapter=self.adapter,
            aimd_controller=self.aimd,
            model_router=self.router,
            event_emitter=self._emit_event,
        )

    def _emit_event(self, event_type, task_id=None, payload=None):
        ev = Event(
            event_type=event_type,
            mission_id="m1",
            task_id=task_id,
            payload=payload or {}
        )
        self.events.append(ev)
        return ev

    def test_active_tasks_strictly_gated_by_adaptive_capacity(self):
        """
        Scenario: 4 idle workers and 4 ready tasks, but adaptive capacity is 2.
        Scheduler must dispatch at most 2 tasks simultaneously, leaving 2 in ReadyQueue.
        """
        # Register 4 workers
        for i in range(4):
            self.worker_registry.register_worker(Worker(worker_id=f"w{i}", domain="backend"))

        # Push 4 ready tasks
        for i in range(4):
            self.ready_queue.push(Task(task_id=f"t{i}", mission_id="m1", title=f"T{i}", domain="backend", status=TaskState.READY))

        self.assertEqual(self.aimd.current_capacity, 2)
        self.assertEqual(len(self.ready_queue), 4)

        # To test active concurrency cleanly without synchronous auto-completion,
        # override adapter with a non-completing / manual completion adapter
        manual_adapter = MockExecutionAdapter(default_success=True)
        # Return None to simulate asynchronous ongoing background execution
        manual_adapter.dispatch = lambda worker, task, profile=None: None

        sched = EventDrivenScheduler(
            ready_queue=self.ready_queue,
            worker_registry=self.worker_registry,
            execution_adapter=manual_adapter,
            aimd_controller=self.aimd,
            model_router=self.router,
        )

        dispatches = sched.evaluate()
        self.assertEqual(len(dispatches), 2)
        self.assertEqual(self.worker_registry.busy_count, 2)
        self.assertEqual(len(self.ready_queue), 2)  # Remaining 2 remain queued!

        # Further evaluation does not dispatch more because capacity is reached
        dispatches2 = sched.evaluate()
        self.assertEqual(len(dispatches2), 0)
        self.assertEqual(self.worker_registry.busy_count, 2)

    def test_capacity_increase_unlocks_queued_tasks(self):
        """
        When capacity increases from 2 to 3, an evaluation step allows 1 additional dispatch.
        """
        for i in range(4):
            self.worker_registry.register_worker(Worker(worker_id=f"w{i}", domain="backend"))
        for i in range(4):
            self.ready_queue.push(Task(task_id=f"t{i}", mission_id="m1", title=f"T{i}", domain="backend", status=TaskState.READY))

        manual_adapter = MockExecutionAdapter()
        manual_adapter.dispatch = lambda w, t, p=None: None

        sched = EventDrivenScheduler(
            ready_queue=self.ready_queue,
            worker_registry=self.worker_registry,
            execution_adapter=manual_adapter,
            aimd_controller=self.aimd,
            model_router=self.router,
        )

        sched.evaluate()
        self.assertEqual(self.worker_registry.busy_count, 2)
        self.assertEqual(len(self.ready_queue), 2)

        # Operator/Controller scales capacity to 3
        self.aimd.force_capacity(3)
        self.assertEqual(self.aimd.current_capacity, 3)

        # Now evaluation should dispatch exactly 1 more task
        dispatches = sched.evaluate()
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(self.worker_registry.busy_count, 3)
        self.assertEqual(len(self.ready_queue), 1)

    def test_rate_limit_failure_triggers_multiplicative_decrease_and_event(self):
        """
        Simulated HTTP 429 triggers multiplicative decrease and emits CAPACITY_CHANGED event.
        """
        w1 = Worker(worker_id="w1", domain="backend")
        self.worker_registry.register_worker(w1)

        t_rl = Task(task_id="t_rl", mission_id="m1", title="Rate Limited Task", domain="backend", status=TaskState.READY)
        self.ready_queue.push(t_rl)

        # Set initial capacity = 4
        self.aimd.force_capacity(4)

        # Configure adapter to return HTTP 429
        self.adapter.rate_limit_tasks.add("t_rl")

        self.scheduler.evaluate()

        # Capacity should have decreased from 4 to max(2, floor(4*0.5)) = 2
        self.assertEqual(self.aimd.current_capacity, 2)
        self.assertEqual(self.aimd.state.total_decreases, 1)

        # Verify CAPACITY_CHANGED event was emitted
        cap_events = [e for e in self.events if e.event_type == EventType.CAPACITY_CHANGED]
        self.assertEqual(len(cap_events), 1)
        self.assertEqual(cap_events[0].payload["old_capacity"], 4)
        self.assertEqual(cap_events[0].payload["new_capacity"], 2)

    def test_dispatch_passes_execution_profile_with_model_tier(self):
        """
        Scheduler queries ModelRouter and attaches ExecutionProfile to ScheduledDispatch.
        """
        w1 = Worker(worker_id="w1", domain="backend")
        self.worker_registry.register_worker(w1)

        t_high = Task(
            task_id="t_high",
            mission_id="m1",
            title="High Priority Task",
            domain="backend",
            priority=9.0,
            status=TaskState.READY,
        )
        self.ready_queue.push(t_high)

        dispatches = self.scheduler.evaluate()
        self.assertEqual(len(dispatches), 1)
        dispatch = dispatches[0]

        self.assertIsNotNone(dispatch.execution_profile)
        self.assertEqual(dispatch.execution_profile.tier, ModelTier.PRO)
        self.assertEqual(dispatch.execution_profile.model_id, "model: pro")

        # Adapter received execution profile
        self.assertEqual(len(self.adapter.dispatches), 1)
        adapter_record = self.adapter.dispatches[0]
        self.assertIsNotNone(adapter_record["execution_profile"])
        self.assertEqual(adapter_record["execution_profile"]["tier"], "PRO")

    def test_worker_shortage_handled_safely(self):
        """
        When adaptive capacity is high (e.g. 4) but only 1 worker is available,
        the scheduler only dispatches 1 task and does not create uncontrolled execution.
        """
        self.aimd.force_capacity(4)
        self.worker_registry.register_worker(Worker(worker_id="w1", domain="backend"))

        self.ready_queue.push(Task(task_id="t1", mission_id="m1", title="T1", domain="backend", status=TaskState.READY))
        self.ready_queue.push(Task(task_id="t2", mission_id="m1", title="T2", domain="backend", status=TaskState.READY))

        manual_adapter = MockExecutionAdapter()
        manual_adapter.dispatch = lambda w, t, p=None: None

        sched = EventDrivenScheduler(
            ready_queue=self.ready_queue,
            worker_registry=self.worker_registry,
            execution_adapter=manual_adapter,
            aimd_controller=self.aimd,
            model_router=self.router,
        )

        dispatches = sched.evaluate()
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(self.worker_registry.busy_count, 1)
        self.assertEqual(self.worker_registry.idle_count, 0)
        self.assertEqual(len(self.ready_queue), 1)  # t2 safely queued waiting for worker


if __name__ == "__main__":
    unittest.main()
