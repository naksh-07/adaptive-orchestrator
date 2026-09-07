"""
Tests for Adaptive Orchestrator v5 Models and State Transitions.
"""

import unittest
from orchestrator.exceptions import InvalidStateTransitionError
from orchestrator.models import (
    Event,
    EventType,
    Mission,
    MissionState,
    Task,
    TaskState,
)


class TestTaskModel(unittest.TestCase):
    def test_task_creation_defaults(self):
        task = Task(task_id="t1", mission_id="m1", title="Task 1")
        self.assertEqual(task.task_id, "t1")
        self.assertEqual(task.mission_id, "m1")
        self.assertEqual(task.title, "Task 1")
        self.assertEqual(task.status, TaskState.PENDING)
        self.assertEqual(task.priority, 0.0)
        self.assertEqual(len(task.dependencies), 0)
        self.assertEqual(len(task.dependents), 0)
        self.assertFalse(task.status.is_terminal)
        self.assertFalse(task.status.is_active)
        self.assertFalse(task.status.is_success)

    def test_valid_state_transitions(self):
        task = Task(task_id="t1", mission_id="m1", title="Task 1")

        # PENDING -> READY
        task.transition_to(TaskState.READY)
        self.assertEqual(task.status, TaskState.READY)

        # READY -> RUNNING
        task.transition_to(TaskState.RUNNING)
        self.assertEqual(task.status, TaskState.RUNNING)
        self.assertIsNotNone(task.started_at)
        self.assertTrue(task.status.is_active)

        # RUNNING -> VERIFYING
        task.transition_to(TaskState.VERIFYING)
        self.assertEqual(task.status, TaskState.VERIFYING)
        self.assertTrue(task.status.is_active)

        # VERIFYING -> PASSED
        task.transition_to(TaskState.PASSED)
        self.assertEqual(task.status, TaskState.PASSED)
        self.assertIsNotNone(task.completed_at)
        self.assertTrue(task.status.is_terminal)
        self.assertTrue(task.status.is_success)

    def test_invalid_state_transitions(self):
        task = Task(task_id="t1", mission_id="m1", title="Task 1")

        # PENDING cannot directly jump to RUNNING without becoming READY
        with self.assertRaises(InvalidStateTransitionError):
            task.transition_to(TaskState.RUNNING)

        # PENDING cannot directly jump to PASSED
        with self.assertRaises(InvalidStateTransitionError):
            task.transition_to(TaskState.PASSED)

        # CANCELLED is terminal; cannot transition to anything
        task.transition_to(TaskState.CANCELLED)
        self.assertTrue(task.status.is_terminal)
        with self.assertRaises(InvalidStateTransitionError):
            task.transition_to(TaskState.READY)

    def test_retry_transitions(self):
        task = Task(task_id="t1", mission_id="m1", title="Task 1")
        task.transition_to(TaskState.READY)
        task.transition_to(TaskState.RUNNING)

        # RUNNING -> RETRYING
        task.transition_to(TaskState.RETRYING)
        self.assertEqual(task.status, TaskState.RETRYING)
        self.assertEqual(task.retry_count, 1)

        # RETRYING -> RUNNING
        task.transition_to(TaskState.RUNNING)
        self.assertEqual(task.status, TaskState.RUNNING)

        # RUNNING -> FAILED
        task.transition_to(TaskState.FAILED)
        self.assertEqual(task.status, TaskState.FAILED)
        self.assertTrue(task.status.is_terminal)

    def test_task_serialization(self):
        task = Task(
            task_id="t1",
            mission_id="m1",
            title="Task 1",
            domain="backend",
            priority=10.0,
            dependencies={"t0"},
        )
        data = task.to_dict()
        self.assertEqual(data["task_id"], "t1")
        self.assertEqual(data["domain"], "backend")
        self.assertEqual(data["priority"], 10.0)
        self.assertEqual(data["dependencies"], ["t0"])


class TestMissionModel(unittest.TestCase):
    def test_mission_lifecycle(self):
        mission = Mission(mission_id="m1", title="Test Mission")
        self.assertEqual(mission.state, MissionState.DRAFTING)

        mission.transition_to(MissionState.PLAN_APPROVED)
        self.assertEqual(mission.state, MissionState.PLAN_APPROVED)

        mission.transition_to(MissionState.EXECUTING)
        self.assertEqual(mission.state, MissionState.EXECUTING)

        mission.transition_to(MissionState.PAUSED)
        self.assertEqual(mission.state, MissionState.PAUSED)

        mission.transition_to(MissionState.EXECUTING)
        self.assertEqual(mission.state, MissionState.EXECUTING)

        mission.transition_to(MissionState.COMPLETED)
        self.assertEqual(mission.state, MissionState.COMPLETED)

    def test_invalid_mission_transitions(self):
        mission = Mission(mission_id="m1", title="Test Mission")
        with self.assertRaises(InvalidStateTransitionError):
            mission.transition_to(MissionState.COMPLETED)  # Cannot jump from DRAFTING to COMPLETED


class TestEventModel(unittest.TestCase):
    def test_event_structure(self):
        event = Event(
            event_type=EventType.TASK_READY,
            mission_id="m1",
            task_id="t1",
            sequence=1,
            payload={"priority": 5.0},
        )
        self.assertEqual(event.event_type, EventType.TASK_READY)
        self.assertEqual(event.mission_id, "m1")
        self.assertEqual(event.task_id, "t1")
        self.assertEqual(event.sequence, 1)
        data = event.to_dict()
        self.assertEqual(data["event_type"], "TASK_READY")
        self.assertEqual(data["payload"]["priority"], 5.0)


if __name__ == "__main__":
    unittest.main()
