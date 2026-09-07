"""
Tests for DependencyResolver in Adaptive Orchestrator v5.
"""

import unittest
from orchestrator.graph.dag import DependencyGraph
from orchestrator.models import Task, TaskState
from orchestrator.resolver import DependencyResolver


class TestDependencyResolver(unittest.TestCase):
    def setUp(self):
        self.graph = DependencyGraph()
        self.resolver = DependencyResolver(self.graph)

    def test_zero_dependency_task_is_ready(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1", status=TaskState.PENDING)
        self.graph.add_task(t1)
        self.assertTrue(self.resolver.is_task_ready("t1"))

    def test_unsatisfied_dependency_is_not_ready(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1", status=TaskState.PENDING)
        t2 = Task(task_id="t2", mission_id="m1", title="Task 2", status=TaskState.PENDING)
        self.graph.add_task(t1)
        self.graph.add_task(t2)
        self.graph.add_dependency("t2", "t1")

        self.assertTrue(self.resolver.is_task_ready("t1"))
        self.assertFalse(self.resolver.is_task_ready("t2"))

    def test_satisfied_dependency_makes_task_ready(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1", status=TaskState.PENDING)
        t2 = Task(task_id="t2", mission_id="m1", title="Task 2", status=TaskState.PENDING)
        self.graph.add_task(t1)
        self.graph.add_task(t2)
        self.graph.add_dependency("t2", "t1")

        # t1 progresses and passes
        t1.transition_to(TaskState.READY)
        t1.transition_to(TaskState.RUNNING)
        t1.transition_to(TaskState.PASSED)

        self.assertTrue(self.resolver.is_task_ready("t2"))

    def test_running_or_passed_tasks_are_not_ready(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1", status=TaskState.PENDING)
        self.graph.add_task(t1)

        t1.transition_to(TaskState.READY)
        t1.transition_to(TaskState.RUNNING)
        self.assertFalse(self.resolver.is_task_ready("t1"))

        t1.transition_to(TaskState.PASSED)
        self.assertFalse(self.resolver.is_task_ready("t1"))

    def test_calculate_unlock_value(self):
        """
             A
           /   \
          B     C (also depends on X)
        """
        for tid in ["A", "B", "C", "X"]:
            self.graph.add_task(Task(task_id=tid, mission_id="m1", title=tid))

        self.graph.add_dependency("B", "A")
        self.graph.add_dependency("C", "A")
        self.graph.add_dependency("C", "X")

        # For A:
        # B depends ONLY on A -> if A passes, B becomes READY (1 unlock)
        # C depends on A and X -> if A passes, C still waits on X (0 unlock)
        # Total unlock value for A = 1
        self.assertEqual(self.resolver.calculate_unlock_value("A"), 1)

        # If X passes first:
        x_task = self.graph.get_task("X")
        x_task.transition_to(TaskState.READY)
        x_task.transition_to(TaskState.RUNNING)
        x_task.transition_to(TaskState.PASSED)

        # Now A is the only remaining blocker for C as well!
        self.assertEqual(self.resolver.calculate_unlock_value("A"), 2)

    def test_resolve_dependents_on_completion(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        t2 = Task(task_id="t2", mission_id="m1", title="Task 2")
        self.graph.add_task(t1)
        self.graph.add_task(t2)
        self.graph.add_dependency("t2", "t1")

        t1.transition_to(TaskState.READY)
        t1.transition_to(TaskState.RUNNING)
        t1.transition_to(TaskState.PASSED)

        newly_ready = self.resolver.resolve_dependents_on_completion("t1")
        self.assertEqual(len(newly_ready), 1)
        self.assertEqual(newly_ready[0].task_id, "t2")
        self.assertEqual(newly_ready[0].status, TaskState.READY)

    def test_resolve_dependents_on_failure(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        t2 = Task(task_id="t2", mission_id="m1", title="Task 2")
        self.graph.add_task(t1)
        self.graph.add_task(t2)
        self.graph.add_dependency("t2", "t1")

        t1.transition_to(TaskState.READY)
        t1.transition_to(TaskState.RUNNING)
        t1.transition_to(TaskState.FAILED)

        newly_blocked = self.resolver.resolve_dependents_on_failure("t1")
        self.assertEqual(len(newly_blocked), 1)
        self.assertEqual(newly_blocked[0].task_id, "t2")
        self.assertEqual(newly_blocked[0].status, TaskState.BLOCKED)


if __name__ == "__main__":
    unittest.main()
