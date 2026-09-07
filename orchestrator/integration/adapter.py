"""
Adaptive Orchestrator v5 - Merge Adapter Boundary.
Encapsulates branch creation, diff inspection, sequential merge execution,
conflict detection, and merge abort. Provides a deterministic mock adapter.
"""

from __future__ import annotations

import os
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set

from orchestrator.integration.models import MergeResult


class MergeAdapter(ABC):
    """
    Abstract adapter boundary for automated Git branch integration.
    """

    @abstractmethod
    def create_integration_branch(
        self,
        base_branch: str = "main",
        branch_name: str = "ao/integration"
    ) -> str:
        """Creates or resets the shared integration branch."""
        pass

    @abstractmethod
    def inspect_diff(self, source_branch: str, target_branch: str) -> str:
        """Inspects diff between source_branch and target_branch."""
        pass

    @abstractmethod
    def attempt_merge(
        self,
        source_branch: str,
        target_branch: str,
        task_id: str,
        commit_message: Optional[str] = None
    ) -> MergeResult:
        """Attempts an automated integration merge from source_branch into target_branch."""
        pass

    @abstractmethod
    def abort_merge(self) -> None:
        """Aborts an active in-progress merge after conflict detection."""
        pass

    @abstractmethod
    def cleanup_branch(self, branch_name: str) -> bool:
        """Deletes a merged or obsolete feature branch."""
        pass


class MockMergeAdapter(MergeAdapter):
    """
    Deterministic in-memory mock merge adapter for unit testing.
    Can be configured to simulate clean merges or conflict scenarios on specific branches.
    """

    def __init__(self) -> None:
        self.integration_branch: str = "ao/integration"
        self.merged_branches: List[str] = []
        self.conflicting_branches: Set[str] = set()
        self.conflict_file_map: Dict[str, List[str]] = {}
        self.aborted_count: int = 0
        self.cleaned_branches: List[str] = []
        self.custom_diffs: Dict[str, str] = {}

    def set_conflict(self, branch_name: str, conflict_files: Optional[List[str]] = None) -> None:
        """Configures a branch to produce a merge conflict upon integration."""
        self.conflicting_branches.add(branch_name)
        self.conflict_file_map[branch_name] = conflict_files or ["src/conflict.py"]

    fail_merge = set_conflict


    def create_integration_branch(
        self,
        base_branch: str = "main",
        branch_name: str = "ao/integration"
    ) -> str:
        self.integration_branch = branch_name
        return branch_name

    def inspect_diff(self, source_branch: str, target_branch: str) -> str:
        return self.custom_diffs.get(
            source_branch,
            f"diff --git a/{source_branch} b/{target_branch}\n# Mock diff"
        )

    def attempt_merge(
        self,
        source_branch: str,
        target_branch: str,
        task_id: str,
        commit_message: Optional[str] = None
    ) -> MergeResult:
        start_time = time.time()
        # Check if simulated conflict
        if source_branch in self.conflicting_branches:
            conflicts = self.conflict_file_map.get(source_branch, ["src/conflict.py"])
            return MergeResult(
                success=False,
                task_id=task_id,
                branch_name=source_branch,
                commit_id=None,
                conflict_files=conflicts,
                error=f"Merge conflict in {', '.join(conflicts)}",
                duration=time.time() - start_time,
            )

        # Successful merge
        self.merged_branches.append(source_branch)
        commit_id = f"commit_{task_id}_{len(self.merged_branches)}"
        return MergeResult(
            success=True,
            task_id=task_id,
            branch_name=source_branch,
            commit_id=commit_id,
            conflict_files=[],
            error=None,
            duration=time.time() - start_time,
        )

    def abort_merge(self) -> None:
        self.aborted_count += 1

    def cleanup_branch(self, branch_name: str) -> bool:
        self.cleaned_branches.append(branch_name)
        return True


class GitMergeAdapter(MergeAdapter):
    """
    Subprocess-driven Git merge adapter executing native git CLI commands.
    Ensures safe non-fast-forward merge and clean abort on conflict.
    """

    def __init__(self, repo_root: Optional[str] = None) -> None:
        self._repo_root = os.path.abspath(repo_root or os.getcwd())

    def _run_git(self, args: List[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git"] + args,
            cwd=self._repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

    def create_integration_branch(
        self,
        base_branch: str = "main",
        branch_name: str = "ao/integration"
    ) -> str:
        # Check if branch exists
        res = self._run_git(["checkout", "-B", branch_name, base_branch])
        return branch_name

    def inspect_diff(self, source_branch: str, target_branch: str) -> str:
        res = self._run_git(["diff", f"{target_branch}...{source_branch}"])
        return res.stdout

    def attempt_merge(
        self,
        source_branch: str,
        target_branch: str,
        task_id: str,
        commit_message: Optional[str] = None
    ) -> MergeResult:
        start_time = time.time()
        # 1. Checkout target integration branch
        chk = self._run_git(["checkout", target_branch])
        if chk.returncode != 0:
            return MergeResult(
                success=False,
                task_id=task_id,
                branch_name=source_branch,
                error=f"Failed to checkout {target_branch}: {chk.stderr.strip()}",
                duration=time.time() - start_time,
            )

        # 2. Attempt git merge --no-ff
        msg = commit_message or f"Merge branch '{source_branch}' for task '{task_id}'"
        res = self._run_git(["merge", "--no-ff", "-m", msg, source_branch])

        if res.returncode != 0:
            # Conflict occurred! Inspect conflict files with git status --porcelain
            status_res = self._run_git(["status", "--porcelain"])
            conflict_files = []
            for line in status_res.stdout.splitlines():
                if line.startswith("UU ") or line.startswith("AA ") or line.startswith("UD "):
                    conflict_files.append(line.split(maxsplit=1)[-1])

            self.abort_merge()

            return MergeResult(
                success=False,
                task_id=task_id,
                branch_name=source_branch,
                commit_id=None,
                conflict_files=conflict_files,
                error=res.stderr.strip() or "Automated git merge conflict",
                duration=time.time() - start_time,
            )

        # 3. Clean merge: extract HEAD commit hash
        rev = self._run_git(["rev-parse", "HEAD"])
        commit_id = rev.stdout.strip() if rev.returncode == 0 else "unknown"

        return MergeResult(
            success=True,
            task_id=task_id,
            branch_name=source_branch,
            commit_id=commit_id,
            conflict_files=[],
            error=None,
            duration=time.time() - start_time,
        )

    def abort_merge(self) -> None:
        self._run_git(["merge", "--abort"])

    def cleanup_branch(self, branch_name: str) -> bool:
        res = self._run_git(["branch", "-D", branch_name])
        return res.returncode == 0
