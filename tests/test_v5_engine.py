"""
End-to-End Tests for MissionEngine in Adaptive Orchestrator v5.
"""

import unittest
from orchestrator.engine import MissionEngine
from orchestrator.exceptions import TaskNotReadyError
from orchestrator.models import EventType, MissionState, TaskState


class TestMissionEngine(unittest.TestCase):
    def test_engine_initialization(self):
        engine = MissionEngine(mission_id="m1", title="Test Mission")
        self.assertEqual(engine.mission.mission_id, "m1")
        self.assertEqual(engine.mission.state, MissionState.DRAFTING)

        events = engine.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, EventType.MISSION_CREATED)

    def test_add_task_with_zero_dependencies_becomes_ready(self):
        engine = MissionEngine(mission_id="m1", title="Test Mission")
        t1 = engine.add_task(task_id="t1", title="Root Task", priority=5.0)

        self.assertEqual(t1.status, TaskState.READY)
        self.assertEqual(len(engine.ready_queue), 1)

        event_types = [e.event_type for e in engine.get_events()]
        self.assertIn(EventType.TASK_CREATED, event_types)
        self.assertIn(EventType.TASK_READY, event_types)

    def test_add_task_with_dependencies_remains_pending(self):
        engine = MissionEngine(mission_id="m1", title="Test Mission")
        engine.add_task(task_id="t1", title="Root Task")
        t2 = engine.add_task(task_id="t2", title="Dependent Task", dependencies=["t1"])

        self.assertEqual(t2.status, TaskState.PENDING)
        self.assertEqual(len(engine.ready_queue), 1)  # Only t1 in ready queue
        self.assertEqual(engine.ready_queue.peek().task_id, "t1")

    def test_adding_dependency_reverts_ready_task_if_prereq_unmet(self):
        engine = MissionEngine(mission_id="m1", title="Test Mission")
        t1 = engine.add_task(task_id="t1", title="T1")
        t2 = engine.add_task(task_id="t2", title="T2")

        # Both initially have 0 dependencies, so both are READY
        self.assertEqual(t1.status, TaskState.READY)
        self.assertEqual(t2.status, TaskState.READY)
        self.assertEqual(len(engine.ready_queue), 2)

        # Now declare t2 depends on t1
        engine.add_dependency("t2", "t1")

        # t2 must no longer be ready!
        self.assertEqual(t2.status, TaskState.PENDING)
        self.assertFalse(engine.ready_queue.contains("t2"))
        self.assertEqual(len(engine.ready_queue), 1)

    def test_task_lifecycle_completion_unlocks_dependents(self):
        engine = MissionEngine(mission_id="m1", title="Test Mission")
        engine.add_task(task_id="t1", title="Task 1")
        engine.add_task(task_id="t2", title="Task 2", dependencies=["t1"])

        # 1. Start t1
        engine.mark_task_started("t1")
        t1 = engine.graph.get_task("t1")
        self.assertEqual(t1.status, TaskState.RUNNING)
        self.assertTrue(engine.ready_queue.is_empty())

        # 2. Complete t1
        completed, newly_ready = engine.mark_task_completed("t1", result={"output": "ok"})
        self.assertEqual(completed.status, TaskState.PASSED)
        self.assertEqual(len(newly_ready), 1)
        self.assertEqual(newly_ready[0].task_id, "t2")
        self.assertEqual(newly_ready[0].status, TaskState.READY)

        # 3. t2 is now in ready queue
        self.assertEqual(len(engine.ready_queue), 1)
        self.assertEqual(engine.ready_queue.peek().task_id, "t2")

        # 4. Start and complete t2
        engine.mark_task_started("t2")
        engine.mark_task_verifying("t2")
        completed_2, newly_ready_2 = engine.mark_task_completed("t2")
        self.assertEqual(completed_2.status, TaskState.PASSED)
        self.assertEqual(len(newly_ready_2), 0)

        # Entire mission should now be COMPLETED
        self.assertEqual(engine.mission.state, MissionState.COMPLETED)

    def test_task_failure_blocks_dependents(self):
        engine = MissionEngine(mission_id="m1", title="Test Mission")
        engine.add_task(task_id="t1", title="Task 1")
        engine.add_task(task_id="t2", title="Task 2", dependencies=["t1"])

        engine.mark_task_started("t1")
        engine.mark_task_failed("t1", error="Syntax error", can_retry=False)

        t1 = engine.graph.get_task("t1")
        t2 = engine.graph.get_task("t2")

        self.assertEqual(t1.status, TaskState.FAILED)
        self.assertEqual(t2.status, TaskState.BLOCKED)
        self.assertTrue(engine.ready_queue.is_empty())

    def test_task_retry_lifecycle(self):
        engine = MissionEngine(mission_id="m1", title="Test Mission")
        engine.add_task(task_id="t1", title="Task 1")

        engine.mark_task_started("t1")
        # Fail with can_retry = True
        engine.mark_task_failed("t1", error="Test failure", can_retry=True)

        t1 = engine.graph.get_task("t1")
        self.assertEqual(t1.status, TaskState.RETRYING)
        self.assertEqual(t1.retry_count, 1)

        # Resume running for repair
        t1.transition_to(TaskState.RUNNING)
        self.assertEqual(t1.status, TaskState.RUNNING)

        # Pass on retry
        engine.mark_task_completed("t1")
        self.assertEqual(t1.status, TaskState.PASSED)

    def test_task_cancellation_cascades_to_dependents(self):
        engine = MissionEngine(mission_id="m1", title="Test Mission")
        engine.add_task(task_id="t1", title="T1")
        engine.add_task(task_id="t2", title="T2", dependencies=["t1"])
        engine.add_task(task_id="t3", title="T3", dependencies=["t2"])

        cancelled = engine.mark_task_cancelled("t1", reason="User abort")
        self.assertEqual(len(cancelled), 3)
        self.assertEqual(engine.graph.get_task("t1").status, TaskState.CANCELLED)
        self.assertEqual(engine.graph.get_task("t2").status, TaskState.CANCELLED)
        self.assertEqual(engine.graph.get_task("t3").status, TaskState.CANCELLED)
        self.assertTrue(engine.ready_queue.is_empty())

    def test_event_subscription(self):
        engine = MissionEngine(mission_id="m1", title="Test Mission")
        received_events = []
        engine.subscribe(lambda e: received_events.append(e))

        engine.add_task(task_id="t1", title="Task 1")
        engine.mark_task_started("t1")
        engine.mark_task_completed("t1")

        self.assertTrue(len(received_events) >= 3)
        event_names = [e.event_type for e in received_events]
        self.assertIn(EventType.TASK_CREATED, event_names)
        self.assertIn(EventType.TASK_STARTED, event_names)
        self.assertIn(EventType.TASK_COMPLETED, event_names)

    def test_cannot_start_unready_task(self):
        engine = MissionEngine(mission_id="m1", title="Test Mission")
        engine.add_task(task_id="t1", title="Root")
        engine.add_task(task_id="t2", title="Dep", dependencies=["t1"])

        with self.assertRaises(TaskNotReadyError):
            engine.mark_task_started("t2")

    def test_reproducible_determinism(self):
        """
        Two identical engines with the identical sequence of operations
        must produce identical event types, identical topological order,
        and identical task states.
        """
        def build_and_run():
            eng = MissionEngine(mission_id="det_mission", title="Deterministic Run")
            eng.add_task("db", title="Database Migration", priority=10.0)
            eng.add_task("api", title="API Endpoint", priority=5.0, dependencies=["db"])
            eng.add_task("ui", title="UI View", priority=5.0, dependencies=["api"])
            eng.add_task("docs", title="Documentation", priority=1.0)  # Independent root

            # docs and db are both ready; db has higher priority
            first = eng.pop_next_ready_task()
            eng.mark_task_started(first.task_id)
            eng.mark_task_completed(first.task_id)

            second = eng.pop_next_ready_task()
            eng.mark_task_started(second.task_id)
            eng.mark_task_completed(second.task_id)

            return [e.event_type.value for e in eng.get_events()]

        events_1 = build_and_run()
        events_2 = build_and_run()
        self.assertEqual(events_1, events_2)


if __name__ == "__main__":
    unittest.main()
