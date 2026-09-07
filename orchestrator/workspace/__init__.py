"""
Adaptive Orchestrator v5 - Workspace Ownership Subsystem.
Enforces isolated worktrees, write-set collision detection, and safe ownership lifecycles.
"""

from orchestrator.workspace.adapter import (
    MockWorktreeAdapter,
    NativeWorktreeAdapter,
    WorktreeAdapter,
)
from orchestrator.workspace.collision import (
    CollisionDetector,
    are_write_sets_overlapping,
    is_path_overlap,
    normalize_path,
)
from orchestrator.workspace.models import (
    WorkspaceMode,
    WorkspaceRecord,
    WorkspaceReleaseState,
)
from orchestrator.workspace.registry import WorkspaceRegistry

__all__ = [
    "WorkspaceMode",
    "WorkspaceReleaseState",
    "WorkspaceRecord",
    "normalize_path",
    "is_path_overlap",
    "are_write_sets_overlapping",
    "CollisionDetector",
    "WorkspaceRegistry",
    "WorktreeAdapter",
    "MockWorktreeAdapter",
    "NativeWorktreeAdapter",
]
