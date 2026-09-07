"""
Tests for ReadyQueue in Adaptive Orchestrator v5.
"""

import unittest
from orchestrator.exceptions import DuplicateQueueEntryError, TaskNotReadyError
from orchestrator.models import Task, TaskState
from orchestrator.scheduler.ready_queue import ReadyQueue


class TestReadyQueue(unittest.TestCase):
    def setUp(self):
        self.queue = ReadyQueue()

    def test_enqueue_and_pop(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1", status=TaskState.READY)
        self.queue.push(t1)
        self.assertEqual(len(self.queue), 1)
        self.assertFalse(self.queue.is_empty())
        self.assertTrue(self.queue.contains("t1"))

        popped = self.queue.pop()
        self.assertEqual(popped.task_id, "t1")
        self.assertEqual(len(self.queue), 0)
        self.assertTrue(self.queue.is_empty())

    def test_reject_non_ready_tasks(self):
        t_pending = Task(task_id="tp", mission_id="m1", title="Pending", status=TaskState.PENDING)
        t_running = Task(task_id="tr", mission_id="m1", title="Running", status=TaskState.RUNNING)
        t_passed = Task(task_id="tok", mission_id="m1", title="Passed", status=TaskState.PASSED)

        with self.assertRaises(TaskNotReadyError):
            self.queue.push(t_pending)

        with self.assertRaises(TaskNotReadyError):
            self.queue.push(t_running)

        with self.assertRaises(TaskNotReadyError):
            self.queue.push(t_passed)

    def test_reject_duplicate_task(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1", status=TaskState.READY)
        self.queue.push(t1)
        with self.assertRaises(DuplicateQueueEntryError):
            self.queue.push(t1)

    def test_priority_ordering(self):
        # Three tasks with different priorities
        t_low = Task(task_id="t_low", mission_id="m1", title="Low", priority=1.0, status=TaskState.READY)
        t_med = Task(task_id="t_med", mission_id="m1", title="Med", priority=5.0, status=TaskState.READY)
        t_high = Task(task_id="t_high", mission_id="m1", title="High", priority=10.0, status=TaskState.READY)

        self.queue.push(t_low)
        self.queue.push(t_high)
        self.queue.push(t_med)

        self.assertEqual(self.queue.pop().task_id, "t_high")
        self.assertEqual(self.queue.pop().task_id, "t_med")
        self.assertEqual(self.queue.pop().task_id, "t_low")

    def test_unlock_value_secondary_ordering(self):
        # Two tasks with equal priority but different unlock values
        t1 = Task(task_id="t1", mission_id="m1", title="T1", priority=5.0, status=TaskState.READY)
        t2 = Task(task_id="t2", mission_id="m1", title="T2", priority=5.0, status=TaskState.READY)

        self.queue.push(t1, unlock_value=1)
        self.queue.push(t2, unlock_value=4)

        # t2 should be popped first because unlock_value 4 > 1
        self.assertEqual(self.queue.pop().task_id, "t2")
        self.assertEqual(self.queue.pop().task_id, "t1")

    def test_fifo_age_tie_breaker(self):
        # Equal priority and equal unlock value: earlier inserted task comes first
        t1 = Task(task_id="t1", mission_id="m1", title="T1", priority=5.0, status=TaskState.READY)
        t2 = Task(task_id="t2", mission_id="m1", title="T2", priority=5.0, status=TaskState.READY)

        self.queue.push(t1, unlock_value=2)
        self.queue.push(t2, unlock_value=2)

        self.assertEqual(self.queue.pop().task_id, "t1")
        self.assertEqual(self.queue.pop().task_id, "t2")

    def test_peek(self):
        t1 = Task(task_id="t1", mission_id="m1", title="T1", priority=5.0, status=TaskState.READY)
        t2 = Task(task_id="t2", mission_id="m1", title="T2", priority=10.0, status=TaskState.READY)

        self.queue.push(t1)
        self.queue.push(t2)

        self.assertEqual(self.queue.peek().task_id, "t2")
        # Ensure peek did not remove
        self.assertEqual(len(self.queue), 2)
        self.assertEqual(self.queue.pop().task_id, "t2")
        self.assertEqual(self.queue.peek().task_id, "t1")

    def test_remove_by_id(self):
        t1 = Task(task_id="t1", mission_id="m1", title="T1", status=TaskState.READY)
        t2 = Task(task_id="t2", mission_id="m1", title="T2", status=TaskState.READY)

        self.queue.push(t1)
        self.queue.push(t2)

        removed = self.queue.remove("t1")
        self.assertEqual(removed.task_id, "t1")
        self.assertFalse(self.queue.contains("t1"))
        self.assertEqual(len(self.queue), 1)

        # Popping should only return t2
        self.assertEqual(self.queue.pop().task_id, "t2")
        self.assertTrue(self.queue.is_empty())

    def test_empty_queue_behavior(self):
        self.assertTrue(self.queue.is_empty())
        self.assertIsNone(self.queue.peek())
        self.assertIsNone(self.queue.pop_optional())

        with self.assertRaises(IndexError):
            self.queue.pop()

    def test_all_tasks_snapshot(self):
        t1 = Task(task_id="t1", mission_id="m1", title="T1", priority=1.0, status=TaskState.READY)
        t2 = Task(task_id="t2", mission_id="m1", title="T2", priority=10.0, status=TaskState.READY)
        t3 = Task(task_id="t3", mission_id="m1", title="T3", priority=5.0, status=TaskState.READY)

        self.queue.push(t1)
        self.queue.push(t2)
        self.queue.push(t3)

        snapshot_ids = [t.task_id for t in self.queue.all_tasks()]
        self.assertEqual(snapshot_ids, ["t2", "t3", "t1"])
        self.assertEqual(self.queue.all_task_ids(), ["t2", "t3", "t1"])


if __name__ == "__main__":
    unittest.main()
