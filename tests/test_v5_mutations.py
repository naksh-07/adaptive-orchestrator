"""
Tests for GraphMutationEngine in Adaptive Orchestrator v5.
"""

import unittest
from orchestrator.exceptions import CycleDetectedError, TaskNotFoundError
from orchestrator.graph.dag import DependencyGraph
from orchestrator.graph.mutations import GraphMutationEngine
from orchestrator.models import Task, TaskState


class TestGraphMutationEngine(unittest.TestCase):
    def setUp(self):
        self.graph = DependencyGraph()
        self.mutations = GraphMutationEngine(self.graph)

    def test_dynamic_add_task_and_dependency(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        self.mutations.add_task(t1)

        t2 = Task(task_id="t2", mission_id="m1", title="Task 2")
        self.mutations.add_task(t2)

        self.mutations.add_dependency(dependent_id="t2", prerequisite_id="t1")
        self.assertIn("t1", self.graph.get_dependencies("t2"))

    def test_dynamic_cycle_rejected(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        t2 = Task(task_id="t2", mission_id="m1", title="Task 2")
        self.mutations.add_task(t1)
        self.mutations.add_task(t2)
        self.mutations.add_dependency("t2", "t1")

        with self.assertRaises(CycleDetectedError):
            self.mutations.add_dependency("t1", "t2")

    def test_transitive_invalidation(self):
        """
        A -> B -> C
        Invalidating B should reset B and C, but leave A untouched.
        """
        ta = Task(task_id="A", mission_id="m1", title="A", status=TaskState.PASSED)
        tb = Task(task_id="B", mission_id="m1", title="B", status=TaskState.PASSED)
        tc = Task(task_id="C", mission_id="m1", title="C", status=TaskState.RUNNING)

        self.graph.add_task(ta)
        self.graph.add_task(tb)
        self.graph.add_task(tc)

        self.graph.add_dependency("B", "A")
        self.graph.add_dependency("C", "B")

        invalidated = self.mutations.invalidate_task("B", reason="Schema changed")

        self.assertEqual(invalidated, ["B", "C"])
        self.assertEqual(self.graph.get_task("A").status, TaskState.PASSED)
        self.assertEqual(self.graph.get_task("B").status, TaskState.PENDING)
        self.assertEqual(self.graph.get_task("C").status, TaskState.PENDING)

    def test_invalidate_nonexistent_task(self):
        with self.assertRaises(TaskNotFoundError):
            self.mutations.invalidate_task("ghost")


if __name__ == "__main__":
    unittest.main()
