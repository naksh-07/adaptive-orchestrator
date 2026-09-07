"""
Tests for DependencyGraph in Adaptive Orchestrator v5.
"""

import unittest
from orchestrator.exceptions import (
    CycleDetectedError,
    DuplicateTaskError,
    SelfDependencyError,
    TaskNotFoundError,
    UnknownDependencyError,
)
from orchestrator.graph.dag import DependencyGraph
from orchestrator.models import Task


class TestDependencyGraph(unittest.TestCase):
    def setUp(self):
        self.graph = DependencyGraph()

    def test_add_and_retrieve_task(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        self.graph.add_task(t1)
        self.assertTrue(self.graph.has_task("t1"))
        self.assertEqual(self.graph.get_task("t1").title, "Task 1")
        self.assertEqual(self.graph.task_count(), 1)

    def test_duplicate_task_rejection(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        self.graph.add_task(t1)
        with self.assertRaises(DuplicateTaskError):
            self.graph.add_task(Task(task_id="t1", mission_id="m1", title="Task 1 Duplicate"))

    def test_task_not_found(self):
        with self.assertRaises(TaskNotFoundError):
            self.graph.get_task("nonexistent")

    def test_add_valid_dependency(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        t2 = Task(task_id="t2", mission_id="m1", title="Task 2")
        self.graph.add_task(t1)
        self.graph.add_task(t2)

        # t2 depends on t1 (t1 -> t2)
        self.graph.add_dependency(dependent_id="t2", prerequisite_id="t1")

        self.assertIn("t1", self.graph.get_dependencies("t2"))
        self.assertIn("t2", self.graph.get_dependents("t1"))

    def test_self_dependency_rejected(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        self.graph.add_task(t1)
        with self.assertRaises(SelfDependencyError):
            self.graph.add_dependency(dependent_id="t1", prerequisite_id="t1")

    def test_missing_dependency_tasks_rejected(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        self.graph.add_task(t1)

        with self.assertRaises(UnknownDependencyError):
            self.graph.add_dependency(dependent_id="t1", prerequisite_id="unknown")

        with self.assertRaises(TaskNotFoundError):
            self.graph.add_dependency(dependent_id="unknown", prerequisite_id="t1")

    def test_direct_cycle_rejected(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        t2 = Task(task_id="t2", mission_id="m1", title="Task 2")
        self.graph.add_task(t1)
        self.graph.add_task(t2)

        self.graph.add_dependency("t2", "t1")  # t1 -> t2
        with self.assertRaises(CycleDetectedError):
            self.graph.add_dependency("t1", "t2")  # t2 -> t1 (Cycle!)

        # Ensure graph state remained intact (t1 -> t2 still exists)
        self.assertIn("t1", self.graph.get_dependencies("t2"))
        self.assertNotIn("t2", self.graph.get_dependencies("t1"))

    def test_multi_node_cycle_rejected(self):
        # A -> B -> C -> D
        tasks = [Task(task_id=f"t{i}", mission_id="m1", title=f"Task {i}") for i in range(1, 5)]
        for t in tasks:
            self.graph.add_task(t)

        self.graph.add_dependency("t2", "t1")  # t1 -> t2
        self.graph.add_dependency("t3", "t2")  # t2 -> t3
        self.graph.add_dependency("t4", "t3")  # t3 -> t4

        # Adding t1 depends on t4 should trigger CycleDetectedError (t4 -> t1)
        with self.assertRaises(CycleDetectedError):
            self.graph.add_dependency("t1", "t4")

    def test_fan_out_and_fan_in(self):
        """
             A
           /   \
          B     C
           \   /
             D
        """
        for tid in ["A", "B", "C", "D"]:
            self.graph.add_task(Task(task_id=tid, mission_id="m1", title=tid))

        self.graph.add_dependency("B", "A")
        self.graph.add_dependency("C", "A")
        self.graph.add_dependency("D", "B")
        self.graph.add_dependency("D", "C")

        # Check ancestors and descendants
        self.assertEqual(self.graph.get_ancestors("D"), {"A", "B", "C"})
        self.assertEqual(self.graph.get_descendants("A"), {"B", "C", "D"})

        # Roots and leaves
        roots = [t.task_id for t in self.graph.get_root_tasks()]
        leaves = [t.task_id for t in self.graph.get_leaf_tasks()]
        self.assertEqual(roots, ["A"])
        self.assertEqual(leaves, ["D"])

        # Topological sort
        order = self.graph.topological_sort()
        self.assertEqual(order[0], "A")
        self.assertEqual(order[-1], "D")
        self.assertIn(order[1], ["B", "C"])
        self.assertIn(order[2], ["B", "C"])

    def test_independent_tasks_topological_sort(self):
        for tid in ["X", "Y", "Z"]:
            self.graph.add_task(Task(task_id=tid, mission_id="m1", title=tid))

        order = self.graph.topological_sort()
        self.assertEqual(len(order), 3)
        self.assertEqual(set(order), {"X", "Y", "Z"})
        # Deterministic sorting means X, Y, Z alphabetical
        self.assertEqual(order, ["X", "Y", "Z"])

    def test_remove_dependency(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        t2 = Task(task_id="t2", mission_id="m1", title="Task 2")
        self.graph.add_task(t1)
        self.graph.add_task(t2)

        self.graph.add_dependency("t2", "t1")
        self.assertTrue(self.graph.remove_dependency("t2", "t1"))
        self.assertFalse(self.graph.remove_dependency("t2", "t1"))

        self.assertEqual(len(self.graph.get_dependencies("t2")), 0)
        self.assertEqual(len(self.graph.get_dependents("t1")), 0)

    def test_validate_integrity(self):
        t1 = Task(task_id="t1", mission_id="m1", title="Task 1")
        t2 = Task(task_id="t2", mission_id="m1", title="Task 2")
        self.graph.add_task(t1)
        self.graph.add_task(t2)
        self.graph.add_dependency("t2", "t1")
        # Should not raise
        self.graph.validate_integrity()


if __name__ == "__main__":
    unittest.main()
