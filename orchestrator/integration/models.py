"""
Adaptive Orchestrator v5 - Integration and Merge Models.
Defines data structures for merge requests, sequential integration queue, and merge results.
"""

from __future__ import annotations

import time
from dataclasses import InitVar, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class MergeStatus(str, Enum):
    """Lifecycle states of a task in the merge queue."""
    PENDING = "PENDING"
    MERGING = "MERGING"
    MERGED = "MERGED"
    SUCCESS = "MERGED"
    CONFLICT = "CONFLICT"
    FAILED = "FAILED"


@dataclass
class MergeRequest:
    """
    Structured request for sequential automated integration of a worktree branch.
    """
    task_id: str
    worker_id: str = "w_default"
    branch_name: str = ""
    workspace_path: str = ""
    source_branch: InitVar[Optional[str]] = None
    target_branch: Optional[str] = None
    write_set: Set[str] = field(default_factory=set)
    enqueued_at: float = field(default_factory=time.time)
    priority: float = 0.0
    status: MergeStatus = MergeStatus.PENDING
    conflict_files: List[str] = field(default_factory=list)
    error: Optional[str] = None
    commit_id: Optional[str] = None
    task: Optional[Any] = None

    def __post_init__(self, source_branch: Optional[str] = None) -> None:
        if source_branch is not None and not self.branch_name:
            self.branch_name = source_branch

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "branch_name": self.branch_name,
            "workspace_path": self.workspace_path,
            "write_set": sorted(list(self.write_set)),
            "enqueued_at": self.enqueued_at,
            "priority": self.priority,
            "status": self.status.value,
            "conflict_files": list(self.conflict_files),
            "error": self.error,
            "commit_id": self.commit_id,
        }


@dataclass
class MergeResult:
    """
    Outcome of an attempted automated branch integration.
    """
    success: bool
    task_id: str
    branch_name: str
    commit_id: Optional[str] = None
    conflict_files: List[str] = field(default_factory=list)
    error: Optional[str] = None
    duration: float = 0.0

    @property
    def status(self) -> MergeStatus:
        if self.success:
            return MergeStatus.MERGED
        if self.conflict_files:
            return MergeStatus.CONFLICT
        return MergeStatus.FAILED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "task_id": self.task_id,
            "branch_name": self.branch_name,
            "commit_id": self.commit_id,
            "conflict_files": list(self.conflict_files),
            "error": self.error,
            "duration": self.duration,
            "status": self.status.value,
        }

