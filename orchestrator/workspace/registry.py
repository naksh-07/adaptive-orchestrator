"""
Adaptive Orchestrator v5 - Workspace Ownership Registry.
Manages exclusive workspace allocations, prevents duplicate ownership,
detects write collisions, and ensures safe cleanup without lock leakage.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from orchestrator.exceptions import (
    WorkspaceAcquisitionError,
    WorkspaceConflictError,
    WorkspaceNotFoundError,
)
from orchestrator.workspace.collision import CollisionDetector, normalize_path
from orchestrator.workspace.models import (
    WorkspaceMode,
    WorkspaceRecord,
    WorkspaceReleaseState,
)

if TYPE_CHECKING:
    from orchestrator.models import Task
    from orchestrator.workers.models import Worker


class WorkspaceRegistry:
    """
    In-memory registry tracking workspace allocations and enforcing write-set safety.
    """

    def __init__(self) -> None:
        self._active_by_task: Dict[str, WorkspaceRecord] = {}
        self._active_by_worker: Dict[str, WorkspaceRecord] = {}
        self._history: List[WorkspaceRecord] = []

    @property
    def active_count(self) -> int:
        """Returns count of currently active workspace allocations."""
        return len(self._active_by_task)

    def can_acquire(self, task: Task) -> Tuple[bool, Optional[str]]:
        """
        Evaluates whether a task can safely acquire workspace ownership right now.
        Returns (True, None) if safe, or (False, reason) if blocked by conflict.
        """
        if task.task_id in self._active_by_task:
            return False, f"Task '{task.task_id}' already has an active workspace ownership"

        conflict = CollisionDetector.detect_active_conflict(task, self._active_by_task.values())
        if conflict:
            conflicting_record, task_path, active_path = conflict
            return False, (
                f"Write conflict: Task '{task.task_id}' path '{task_path}' overlaps with "
                f"active task '{conflicting_record.task_id}' path '{active_path}'"
            )

        return True, None

    def is_locked(self, task_id: str) -> bool:
        """Returns True if task_id holds an active workspace lock."""
        return task_id in self._active_by_task

    def get_active_locks(self) -> List[WorkspaceRecord]:
        """Returns snapshot list of currently active workspace locks/records."""
        return list(self._active_by_task.values())

    def get_active_records(self) -> List[WorkspaceRecord]:
        """Returns snapshot list of currently active workspace records."""
        return list(self._active_by_task.values())

    def acquire(
        self,
        task: Optional[Any] = None,
        worker: Optional[Any] = None,
        workspace_mode: Optional[str] = None,
        workspace_path: Optional[str] = None,
        branch_name: Optional[str] = None,
        worker_id: Optional[str] = None,
        task_id: Optional[str] = None,
        mode: Optional[Any] = None,
        read_set: Optional[Set[str]] = None,
        write_set: Optional[Set[str]] = None,
    ) -> WorkspaceRecord:
        """
        Acquires exclusive workspace ownership for a task and worker.
        Raises WorkspaceConflictError if a write-collision exists.
        Raises WorkspaceAcquisitionError if duplicate ownership is attempted.
        """
        tid = task_id or (getattr(task, "task_id", str(task)) if task else None)
        if not tid:
            raise ValueError("Must provide task or task_id to acquire workspace")

        w_id = worker_id or (getattr(worker, "worker_id", str(worker)) if worker else "w_default")

        # 1. Duplicate ownership validation
        if tid in self._active_by_task:
            raise WorkspaceAcquisitionError(
                f"Task '{tid}' already holds active workspace ownership"
            )
        if w_id in self._active_by_worker:
            raise WorkspaceAcquisitionError(
                f"Worker '{w_id}' already holds active workspace ownership"
            )

        task_read_set = read_set if read_set is not None else getattr(task, "read_set", set())
        task_write_set = write_set if write_set is not None else getattr(task, "write_set", set())

        # If task object is not a Task instance, build dummy task object for collision detection
        if hasattr(task, "task_id"):
            task_obj = task
        else:
            from orchestrator.models import Task
            task_obj = Task(id=tid, description="ad-hoc", read_set=task_read_set, write_set=task_write_set)

        # 2. Collision detection
        conflict = CollisionDetector.detect_active_conflict(task_obj, self._active_by_task.values())
        if conflict:
            conflicting_record, task_path, active_path = conflict
            raise WorkspaceConflictError(
                f"Write conflict: Task '{tid}' path '{task_path}' overlaps with "
                f"active task '{conflicting_record.task_id}' path '{active_path}'"
            )

        eff_mode = mode or workspace_mode or getattr(task, "workspace_mode", WorkspaceMode.BRANCH.value)
        if hasattr(eff_mode, "value"):
            eff_mode = eff_mode.value
        path = workspace_path or f".worktrees/{tid}"
        branch = branch_name or f"ao/{tid}"

        # 3. Create record with normalized paths
        normalized_reads = {normalize_path(p) for p in task_read_set if p}
        normalized_writes = {normalize_path(p) for p in task_write_set if p}

        record = WorkspaceRecord(
            task_id=tid,
            worker_id=w_id,
            workspace_mode=eff_mode,
            workspace_path=path,
            branch_name=branch,
            read_set=normalized_reads,
            write_set=normalized_writes,
            acquired_at=time.time(),
            release_state=WorkspaceReleaseState.ACTIVE,
        )

        self._active_by_task[tid] = record
        self._active_by_worker[w_id] = record
        return record

    def release(
        self,
        task_id: str,
        state: WorkspaceReleaseState = WorkspaceReleaseState.RELEASED,
        reason: str = "",
        raise_if_missing: bool = True,
    ) -> Optional[WorkspaceRecord]:
        """
        Releases the workspace ownership for a task.
        Safely removes the ownership lock and records it into history.
        """
        record = self._active_by_task.pop(task_id, None)
        if record is None:
            # Idempotent duplicate release: if task was already released in history, return None safely
            if any(r.task_id == task_id for r in self._history):
                return None
            if raise_if_missing:
                raise WorkspaceNotFoundError(f"No active workspace ownership found for task '{task_id}'")
            return None

        # Also remove worker mapping
        self._active_by_worker.pop(record.worker_id, None)

        record.release(state=state, reason=reason)
        self._history.append(record)
        return record

    def release_worker_lock(self, worker_id: str) -> None:
        """
        Releases the execution lock held by a worker without affecting task ownership.
        Allows the warm worker to be reused for other tasks while the completed task
        remains in the merge queue.
        """
        self._active_by_worker.pop(worker_id, None)


    def release_for_worker(
        self,
        worker_id: str,
        state: WorkspaceReleaseState = WorkspaceReleaseState.FAILED,
        reason: str = ""
    ) -> List[WorkspaceRecord]:
        """
        Releases any workspace ownership held by the given worker.
        Critical for worker failure recovery so no stale locks survive.
        """
        record = self._active_by_worker.pop(worker_id, None)
        if record is None:
            return []

        self._active_by_task.pop(record.task_id, None)
        record.release(state=state, reason=reason)
        self._history.append(record)
        return [record]

    def has_active_ownership(self, task_id: str) -> bool:
        """Returns True if task_id holds an active workspace ownership."""
        return task_id in self._active_by_task

    def get_record(self, task_id: str) -> Optional[WorkspaceRecord]:
        """Returns the most recent workspace record for a task (active or historic)."""
        if task_id in self._active_by_task:
            return self._active_by_task[task_id]
        for r in reversed(self._history):
            if r.task_id == task_id:
                return r
        return None

    def cleanup_stale_ownership(
        self,
        max_age_seconds: Optional[float] = None
    ) -> List[WorkspaceRecord]:
        """
        Cleans up stale or expired workspace ownerships.
        If max_age_seconds is provided, releases any active ownership older than that limit.
        """
        now = time.time()
        released: List[WorkspaceRecord] = []

        if max_age_seconds is not None:
            task_ids = list(self._active_by_task.keys())
            for tid in task_ids:
                rec = self._active_by_task.get(tid)
                if rec and (now - rec.acquired_at) > max_age_seconds:
                    rel = self.release(
                        tid,
                        state=WorkspaceReleaseState.INVALIDATED,
                        reason=f"Ownership exceeded max age ({max_age_seconds}s)"
                    )
                    if rel:
                        released.append(rel)

        return released

    cleanup_stale_ownerships = cleanup_stale_ownership

    def get_active_by_task(self, task_id: str) -> Optional[WorkspaceRecord]:
        """Returns the active workspace record for a task if present."""
        return self._active_by_task.get(task_id)

    def get_active_by_worker(self, worker_id: str) -> Optional[WorkspaceRecord]:
        """Returns the active workspace record for a worker if present."""
        return self._active_by_worker.get(worker_id)

    def get_active_records(self) -> List[WorkspaceRecord]:
        """Returns a snapshot of all currently active workspace records."""
        return list(self._active_by_task.values())

    def get_history(self) -> List[WorkspaceRecord]:
        """Returns a snapshot of all historical released workspace records."""
        return list(self._history)

    def snapshot(self) -> Dict[str, Any]:
        """Returns full serializable snapshot of active and historic allocations."""
        records_map = {r.task_id: r.to_dict() for r in list(self._active_by_task.values()) + self._history}
        return {
            "total_records": len(records_map),
            "active_records": len(self._active_by_task),
            "historical_records": len(self._history),
            "records": records_map,
        }

    def clear(self) -> None:
        """Clears all registry state."""
        self._active_by_task.clear()
        self._active_by_worker.clear()
        self._history.clear()

