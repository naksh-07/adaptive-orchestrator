"""
Tests for Task Read/Write Contracts, Path Normalization, and Collision Detection.
"""

import unittest
from orchestrator.models import Task, TaskState
from orchestrator.workspace.collision import (
    CollisionDetector,
    are_write_sets_overlapping,
    is_path_overlap,
    normalize_path,
)


class TestPathNormalization(unittest.TestCase):
    def test_basic_normalization(self):
        self.assertEqual(normalize_path("src/utils.py"), "src/utils.py")
        self.assertEqual(normalize_path("src//utils.py"), "src/utils.py")
        self.assertEqual(normalize_path("./src/utils.py"), "src/utils.py")
        self.assertEqual(normalize_path("src/../src/utils.py"), "src/utils.py")
        self.assertEqual(normalize_path("src\\utils.py"), "src/utils.py")
        self.assertEqual(normalize_path("src/dir/"), "src/dir")

    def test_case_normalization(self):
        # Path comparisons should be normalized cleanly
        self.assertEqual(normalize_path("SRC/utils.py"), "src/utils.py")
        self.assertEqual(normalize_path(""), ".")


class TestPathOverlap(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(is_path_overlap("src/a.py", "src/a.py"))
        self.assertFalse(is_path_overlap("src/a.py", "src/b.py"))

    def test_parent_child_overlap(self):
        # Parent directory overlaps with child file
        self.assertTrue(is_path_overlap("src", "src/a.py"))
        self.assertTrue(is_path_overlap("src/a.py", "src"))
        self.assertTrue(is_path_overlap("src/api", "src/api/routes.py"))
        self.assertTrue(is_path_overlap("src/api/routes.py", "src/api"))

    def test_sibling_directory_no_overlap(self):
        self.assertFalse(is_path_overlap("src/api", "src/api_v2"))
        self.assertFalse(is_path_overlap("src/api_v2", "src/api"))
        self.assertFalse(is_path_overlap("src/app.py", "src/app_test.py"))

    def test_glob_overlap(self):
        self.assertTrue(is_path_overlap("src/*.py", "src/main.py"))
        self.assertTrue(is_path_overlap("src/main.py", "src/*.py"))
        self.assertFalse(is_path_overlap("src/*.py", "tests/test_main.py"))


class TestWriteSetOverlap(unittest.TestCase):
    def test_disjoint_sets(self):
        set_a = {"src/a.py", "src/b.py"}
        set_b = {"src/c.py", "src/d.py"}
        self.assertFalse(are_write_sets_overlapping(set_a, set_b))

    def test_exact_overlap(self):
        set_a = {"src/a.py", "package.json"}
        set_b = {"package.json", "src/c.py"}
        self.assertTrue(are_write_sets_overlapping(set_a, set_b))

    def test_directory_file_overlap(self):
        set_a = {"src/api"}
        set_b = {"src/api/routes.py"}
        self.assertTrue(are_write_sets_overlapping(set_a, set_b))

    def test_empty_sets(self):
        self.assertFalse(are_write_sets_overlapping(set(), {"src/a.py"}))
        self.assertFalse(are_write_sets_overlapping({"src/a.py"}, set()))
        self.assertFalse(are_write_sets_overlapping(set(), set()))


class TestCollisionDetector(unittest.TestCase):
    def setUp(self):
        self.detector = CollisionDetector()

    def test_tasks_disjoint_writers(self):
        task_a = Task(
            task_id="t1",
            title="Task A",
            read_set={"docs/readme.md"},
            write_set={"src/module_a.py"},
        )
        task_b = Task(
            task_id="t2",
            title="Task B",
            read_set={"src/module_a.py"},
            write_set={"src/module_b.py"},
        )
        # B reads what A writes, but their write sets are disjoint -> no write conflict
        can_run, reason = self.detector.can_execute_concurrently(task_a, task_b)
        self.assertTrue(can_run)
        self.assertEqual(reason, "")

    def test_tasks_conflicting_writers(self):
        task_a = Task(
            task_id="t1",
            title="Task A",
            write_set={"src/config.py"},
        )
        task_b = Task(
            task_id="t2",
            title="Task B",
            write_set={"src/config.py"},
        )
        can_run, reason = self.detector.can_execute_concurrently(task_a, task_b)
        self.assertFalse(can_run)
        self.assertIn("Write set collision", reason)

    def test_parent_directory_collision(self):
        task_a = Task(
            task_id="t1",
            title="Task A",
            write_set={"src/components"},
        )
        task_b = Task(
            task_id="t2",
            title="Task B",
            write_set={"src/components/Button.tsx"},
        )
        can_run, reason = self.detector.can_execute_concurrently(task_a, task_b)
        self.assertFalse(can_run)
        self.assertIn("Write set collision", reason)

    def test_active_tasks_conflict_check(self):
        t1 = Task(task_id="t1", title="T1", write_set={"src/auth/service.py"})
        t2 = Task(task_id="t2", title="T2", write_set={"src/billing/service.py"})
        active = [t1, t2]

        # Candidate with disjoint write set
        candidate_ok = Task(task_id="t3", title="T3", write_set={"src/notifications/service.py"})
        has_conflict, conf_id, reason = self.detector.has_write_conflict(candidate_ok, active)
        self.assertFalse(has_conflict)
        self.assertEqual(conf_id, "")

        # Candidate with conflicting write set with T1
        candidate_conf = Task(task_id="t4", title="T4", write_set={"src/auth/routes.py", "src/auth"})
        has_conflict, conf_id, reason = self.detector.has_write_conflict(candidate_conf, active)
        self.assertTrue(has_conflict)
        self.assertEqual(conf_id, "t1")


if __name__ == "__main__":
    unittest.main()
