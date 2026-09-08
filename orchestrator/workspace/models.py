"""
Adaptive Orchestrator v5 - Workspace Ownership Models.
Defines workspace modes, release states, and ownership records for safe parallel execution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Set


class WorkspaceMode(str, Enum):
    """
    Workspace isolation modes aligned with Antigravity runtime capabilities.
    """
    BRANCH = "branch"      # Ephemeral Git worktree for isolated parallel writes
    SHARE = "share"        # Shared directory for read-only parallel tasks
    INHERIT = "inherit"    # Inherit CWD for single-writer serialization or integration
    IN_PLACE = "in_place"  # In-place execution mode


class WorkspaceIsolationType(str, Enum):
    """
    Distinguishes native Antigravity workspace isolation from internal Git worktrees.
    Prevents false claims about platform-level features.
    """
    NATIVE_ANTIGRAVITY = "native_antigravity"      # Runtime manages worktree via invoke_subagent(Workspace='branch')
    INTERNAL_GIT_WORKTREE = "internal_git_worktree"  # Python subprocess manages worktree via git worktree add
    SHARED_FILESYSTEM = "shared_filesystem"          # Shared workspace without worktree isolation


class WorkspaceReleaseState(str, Enum):
    """
    Lifecycle and release state for a workspace ownership allocation.
    """
    ACTIVE = "active"
    RELEASED = "released"
    INVALIDATED = "invalidated"
    MERGED = "merged"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class WorkspaceRecord:
    """
    Ownership record tracking exclusive allocation of a workspace to a task and worker.
    """
    task_id: str
    worker_id: str
    workspace_mode: str = WorkspaceMode.BRANCH.value
    workspace_path: str = ""
    branch_name: Optional[str] = None
    isolation_type: str = WorkspaceIsolationType.INTERNAL_GIT_WORKTREE.value
    read_set: Set[str] = field(default_factory=set)
    write_set: Set[str] = field(default_factory=set)
    acquired_at: float = field(default_factory=time.time)
    released_at: Optional[float] = None
    release_state: WorkspaceReleaseState = WorkspaceReleaseState.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """Indicates whether this workspace ownership is actively held."""
        return self.release_state == WorkspaceReleaseState.ACTIVE

    def release(
        self,
        state: WorkspaceReleaseState = WorkspaceReleaseState.RELEASED,
        reason: str = ""
    ) -> None:
        """Releases the workspace ownership with the specified terminal state."""
        self.release_state = state
        self.released_at = time.time()
        if reason:
            self.metadata["release_reason"] = reason

    def to_dict(self) -> Dict[str, Any]:
        """Serializes workspace record to dictionary."""
        return {
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "workspace_mode": self.workspace_mode,
            "isolation_type": self.isolation_type,
            "workspace_path": self.workspace_path,
            "branch_name": self.branch_name,
            "read_set": sorted(list(self.read_set)),
            "write_set": sorted(list(self.write_set)),
            "acquired_at": self.acquired_at,
            "released_at": self.released_at,
            "release_state": self.release_state.value,
            "is_active": self.is_active,
            "metadata": dict(self.metadata),
        }
