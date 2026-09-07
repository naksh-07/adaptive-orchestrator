"""
Adaptive Orchestrator v5 - Worktree Adapter Abstraction.
Encapsulates Git worktree lifecycle operations, isolating the scheduler
from CLI and runtime process mechanics. Provides a deterministic mock adapter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from orchestrator.exceptions import WorkspaceError

if TYPE_CHECKING:
    from orchestrator.models import Task
    from orchestrator.workers.models import Worker


class WorktreeAdapter(ABC):
    """
    Abstract boundary for isolated workspace / worktree provisioning and lifecycle.
    """

    @abstractmethod
    def create_workspace(
        self,
        task: Task,
        worker: Worker,
        branch_name: Optional[str] = None
    ) -> str:
        """Provisions an isolated workspace/worktree, returning its filesystem path."""
        pass

    @abstractmethod
    def attach_task(self, workspace_path: str, task: Task) -> None:
        """Attaches and initializes task context within the provisioned workspace."""
        pass

    @abstractmethod
    def inspect_status(self, workspace_path: str) -> Dict[str, Any]:
        """Inspects status of the workspace (modified files, clean state, active branch)."""
        pass

    @abstractmethod
    def inspect_diff(self, workspace_path: str, base_branch: str = "main") -> str:
        """Returns the diff of changes made in the workspace relative to base_branch."""
        pass

    @abstractmethod
    def finalize_workspace(
        self,
        workspace_path: str,
        commit_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """Finalizes and commits changes in the workspace for integration."""
        pass

    @abstractmethod
    def cleanup_workspace(
        self,
        workspace_path: str,
        branch_name: Optional[str] = None
    ) -> bool:
        """Cleans up and removes the workspace/worktree from the host filesystem."""
        pass


class MockWorktreeAdapter(WorktreeAdapter):
    """
    Deterministic in-memory mock adapter for unit tests and simulation.
    """

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir: str = base_dir or ".worktrees"
        self.workspaces: Dict[str, Dict[str, Any]] = {}
        self.created_count: int = 0
        self.cleaned_count: int = 0
        self.finalized_count: int = 0
        self.should_fail_create: bool = False
        self._fail_create_reason: Optional[str] = None
        self.should_fail_cleanup: bool = False
        self.custom_diff: Optional[str] = None

    def fail_next_create(self, reason: str = "Simulated creation failure") -> None:
        self.should_fail_create = True
        self._fail_create_reason = reason

    def has_workspace(self, workspace_path: str) -> bool:
        return workspace_path in self.workspaces

    def set_modified_files(self, workspace_path: str, files: List[str]) -> None:
        if workspace_path not in self.workspaces:
            raise WorkspaceError(f"Workspace path '{workspace_path}' not found")
        self.workspaces[workspace_path]["files"] = {f: "content" for f in files}
        self.workspaces[workspace_path]["status"] = "dirty" if files else "clean"

    def set_diff(self, workspace_path: str, diff: str) -> None:
        if workspace_path in self.workspaces:
            self.workspaces[workspace_path]["diff"] = diff

    def create_workspace(
        self,
        task: Any,
        worker: Optional[Any] = None,
        branch_name: Optional[str] = None
    ) -> str:
        task_id = getattr(task, "task_id", str(task))
        worker_id = getattr(worker, "worker_id", (str(worker) if worker else "w_mock"))

        if self.should_fail_create:
            self.should_fail_create = False
            raise WorkspaceError(self._fail_create_reason or f"Simulated worktree creation failure for task '{task_id}'")

        branch = branch_name or f"feat/{task_id}"
        workspace_path = f"{self.base_dir}/{task_id}"

        if workspace_path in self.workspaces:
            existing = self.workspaces[workspace_path]
            if existing.get("task_id") == task_id and existing.get("worker_id") != worker_id:
                existing["worker_id"] = worker_id
                existing["status"] = "clean"
                return workspace_path
            raise WorkspaceError(f"Workspace at '{workspace_path}' already exists")


        self.workspaces[workspace_path] = {
            "task_id": task_id,
            "worker_id": worker_id,
            "branch_name": branch,
            "attached_task": task_id,
            "status": "clean",
            "files": {},
            "is_finalized": False,
        }
        self.created_count += 1
        return workspace_path

    def attach_task(self, workspace_path: str, task: Task) -> None:
        if workspace_path not in self.workspaces:
            raise WorkspaceError(f"Workspace path '{workspace_path}' not found")
        self.workspaces[workspace_path]["attached_task"] = task.task_id

    def inspect_status(self, workspace_path: str) -> Dict[str, Any]:
        if workspace_path not in self.workspaces:
            raise WorkspaceError(f"Workspace path '{workspace_path}' not found")

        ws = self.workspaces[workspace_path]
        return {
            "exists": True,
            "clean": ws.get("status") == "clean",
            "branch": ws.get("branch_name"),
            "modified_files": list(ws.get("files", {}).keys()),
            "is_finalized": ws.get("is_finalized", False),
            "finalized": ws.get("is_finalized", False),
        }

    def inspect_diff(self, workspace_path: str, base_branch: str = "main") -> str:
        if workspace_path in self.workspaces and "diff" in self.workspaces[workspace_path]:
            return self.workspaces[workspace_path]["diff"]
        if self.custom_diff is not None:
            return self.custom_diff
        ws = self.workspaces.get(workspace_path)
        if not ws:
            return ""
        return f"diff --git a/file b/file\n# Mock diff for {ws.get('task_id')}"

    def finalize_workspace(
        self,
        workspace_path: str,
        commit_message: Optional[str] = None
    ) -> Dict[str, Any]:
        if workspace_path not in self.workspaces:
            raise WorkspaceError(f"Workspace path '{workspace_path}' not found")

        ws = self.workspaces[workspace_path]
        ws["is_finalized"] = True
        ws["status"] = "clean"
        ws["files"] = {}
        ws["commit_id"] = f"mock_commit_{ws.get('task_id')}"
        ws["commit_message"] = commit_message or f"Completed task {ws.get('task_id')}"
        self.finalized_count += 1
        return {
            "commit_id": ws["commit_id"],
            "branch": ws.get("branch_name"),
            "workspace_path": workspace_path,
        }

    def cleanup_workspace(
        self,
        workspace_path: str,
        branch_name: Optional[str] = None
    ) -> bool:
        if self.should_fail_cleanup:
            return False

        if workspace_path in self.workspaces:
            del self.workspaces[workspace_path]
            self.cleaned_count += 1
            return True
        return False



class NativeWorktreeAdapter(WorktreeAdapter):
    """
    Native Git worktree adapter wrapping Git CLI subprocess operations.
    Hides Windows/POSIX CLI mechanics and ensures path safety.
    """

    def __init__(self, repo_root: Optional[str] = None) -> None:
        self._repo_root = os.path.abspath(repo_root or os.getcwd())

    def _run_git(self, args: List[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess[str]:
        target_cwd = cwd or self._repo_root
        return subprocess.run(
            ["git"] + args,
            cwd=target_cwd,
            capture_output=True,
            text=True,
            check=False,
        )

    def create_workspace(
        self,
        task: Task,
        worker: Worker,
        branch_name: Optional[str] = None
    ) -> str:
        branch = branch_name or f"ao/{task.task_id}"
        worktree_rel = os.path.join(".worktrees", task.task_id)
        worktree_abs = os.path.abspath(os.path.join(self._repo_root, worktree_rel))

        # Check if worktree directory already exists on disk
        if os.path.exists(worktree_abs):
            self.cleanup_workspace(worktree_abs, branch_name=branch)

        # Run git worktree add -B <branch> <path>
        cmd = ["worktree", "add", "-B", branch, worktree_abs]
        res = self._run_git(cmd)
        if res.returncode != 0:
            raise WorkspaceError(
                f"Failed to create native Git worktree at '{worktree_abs}': {res.stderr.strip()}"
            )

        return worktree_abs

    def attach_task(self, workspace_path: str, task: Task) -> None:
        if not os.path.exists(workspace_path):
            raise WorkspaceError(f"Workspace path does not exist: {workspace_path}")

    def inspect_status(self, workspace_path: str) -> Dict[str, Any]:
        if not os.path.exists(workspace_path):
            return {"exists": False, "clean": True, "modified_files": []}

        res = self._run_git(["status", "--porcelain"], cwd=workspace_path)
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        modified = [line.split(maxsplit=1)[-1] for line in lines]
        return {
            "exists": True,
            "clean": len(modified) == 0,
            "modified_files": modified,
        }

    def inspect_diff(self, workspace_path: str, base_branch: str = "main") -> str:
        res = self._run_git(["diff", base_branch], cwd=workspace_path)
        return res.stdout

    def finalize_workspace(
        self,
        workspace_path: str,
        commit_message: Optional[str] = None
    ) -> Dict[str, Any]:
        msg = commit_message or "Automated task completion commit"
        self._run_git(["add", "-A"], cwd=workspace_path)
        res = self._run_git(["commit", "-m", msg], cwd=workspace_path)
        rev = self._run_git(["rev-parse", "HEAD"], cwd=workspace_path)
        commit_id = rev.stdout.strip() if rev.returncode == 0 else "unknown"
        return {
            "commit_id": commit_id,
            "workspace_path": workspace_path,
        }

    def cleanup_workspace(
        self,
        workspace_path: str,
        branch_name: Optional[str] = None
    ) -> bool:
        # 1. Run git worktree remove --force <path>
        self._run_git(["worktree", "remove", "--force", workspace_path])

        # 2. Safety cleanup if directory remained
        if os.path.exists(workspace_path):
            try:
                shutil.rmtree(workspace_path, ignore_errors=True)
            except Exception:
                pass

        return not os.path.exists(workspace_path)
