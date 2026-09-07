"""
Tests for WorktreeAdapter and MockWorktreeAdapter.
"""

import unittest
from orchestrator.exceptions import WorkspaceError
from orchestrator.workspace.adapter import MockWorktreeAdapter


class TestMockWorktreeAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = MockWorktreeAdapter(base_dir="/mock/worktrees")

    def test_create_workspace(self):
        ws_path = self.adapter.create_workspace("task-123", "feature/task-123")
        self.assertIn("task-123", ws_path)
        self.assertTrue(self.adapter.has_workspace(ws_path))

    def test_duplicate_create_fails(self):
        ws_path = self.adapter.create_workspace("task-123", "feature/task-123")
        with self.assertRaises(WorkspaceError):
            self.adapter.create_workspace("task-123", "feature/task-123")

    def test_inspect_status_and_diff(self):
        ws_path = self.adapter.create_workspace("task-1", "b1")
        self.adapter.set_modified_files(ws_path, ["src/a.py", "src/b.py"])
        self.adapter.set_diff(ws_path, "diff --git a/src/a.py b/src/a.py\n+new line")

        status = self.adapter.inspect_status(ws_path)
        self.assertEqual(status["modified_files"], ["src/a.py", "src/b.py"])
        self.assertFalse(status["clean"])

        diff = self.adapter.inspect_diff(ws_path)
        self.assertIn("+new line", diff)

    def test_finalize_workspace(self):
        ws_path = self.adapter.create_workspace("task-1", "b1")
        self.adapter.set_modified_files(ws_path, ["src/a.py"])
        self.adapter.finalize_workspace(ws_path, commit_message="Implement task 1")

        status = self.adapter.inspect_status(ws_path)
        self.assertTrue(status["finalized"])
        self.assertTrue(status["clean"])

    def test_cleanup_workspace(self):
        ws_path = self.adapter.create_workspace("task-1", "b1")
        self.assertTrue(self.adapter.has_workspace(ws_path))

        self.adapter.cleanup_workspace(ws_path, branch_name="b1")
        self.assertFalse(self.adapter.has_workspace(ws_path))

    def test_failure_simulation(self):
        self.adapter.fail_next_create("Simulated disk full")
        with self.assertRaises(WorkspaceError) as ctx:
            self.adapter.create_workspace("task-x", "bx")
        self.assertIn("Simulated disk full", str(ctx.exception))

    def test_status_non_existent_workspace(self):
        with self.assertRaises(WorkspaceError):
            self.adapter.inspect_status("/non/existent/path")


if __name__ == "__main__":
    unittest.main()
