"""
Adaptive Orchestrator v5 - Sequential Controlled Merge Queue.
Enforces serial branch integration, one merge at a time, emitting structured
lifecycle events and managing post-merge workspace cleanup and failure retention.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set

from orchestrator.exceptions import MergeQueueError
from orchestrator.integration.adapter import MergeAdapter, MockMergeAdapter
from orchestrator.integration.models import MergeRequest, MergeResult, MergeStatus
from orchestrator.models import Event, EventType, TaskState

from orchestrator.workspace.models import WorkspaceReleaseState

if TYPE_CHECKING:
    from orchestrator.models import Task
    from orchestrator.workspace.adapter import WorktreeAdapter
    from orchestrator.workspace.registry import WorkspaceRegistry


class MergeQueue:
    """
    Controlled sequential merge queue.
    Guarantees: Multiple agents execute in parallel; Git integration remains strictly serialized.
    """

    def __init__(
        self,
        adapter: Optional[MergeAdapter] = None,
        workspace_registry: Optional[WorkspaceRegistry] = None,
        worktree_adapter: Optional[WorktreeAdapter] = None,
        integration_branch: str = "ao/integration",
        event_emitter: Optional[Callable[[EventType, Optional[str], Optional[Dict[str, Any]]], Event]] = None,
        on_merge_completed: Optional[Callable[[str, MergeResult], None]] = None,
        on_merge_failed: Optional[Callable[[str, MergeResult], None]] = None,
        merge_adapter: Optional[MergeAdapter] = None,
        target_branch: Optional[str] = None,
    ) -> None:
        self._adapter = merge_adapter or adapter or MockMergeAdapter()
        self._workspace_registry = workspace_registry
        self._worktree_adapter = worktree_adapter
        self._integration_branch = target_branch or integration_branch
        self._event_emitter = event_emitter
        self._on_merge_completed = on_merge_completed
        self._on_merge_failed = on_merge_failed

        self._queue: List[MergeRequest] = []
        self._active_request: Optional[MergeRequest] = None
        self._history: List[MergeRequest] = []
        self._results: Dict[str, MergeResult] = {}
        self._is_merging: bool = False

    @property
    def adapter(self) -> MergeAdapter:
        return self._adapter

    @property
    def is_merging(self) -> bool:
        """Returns True if a merge is actively being executed."""
        return self._is_merging

    @property
    def pending_count(self) -> int:
        """Number of requests waiting in the queue."""
        return len(self._queue)

    @property
    def active_request(self) -> Optional[MergeRequest]:
        """Currently executing merge request, if any."""
        return self._active_request

    @property
    def active_merge(self) -> Optional[MergeRequest]:
        """Alias for active_request."""
        return self._active_request

    @property

    def integration_branch(self) -> str:
        return self._integration_branch

    def get_result(self, task_id: str) -> Optional[MergeResult]:
        """Returns result of a processed merge request."""
        return self._results.get(task_id)

    def get_pending_requests(self) -> List[MergeRequest]:
        """Returns snapshot of pending requests."""
        return list(self._queue)

    def get_history(self) -> List[MergeRequest]:
        """Returns snapshot of processed requests."""
        return list(self._history)

    def enqueue(
        self,
        task: Any,
        worker_id: Optional[str] = None,
        branch_name: Optional[str] = None,
        workspace_path: Optional[str] = None,
        priority: Optional[float] = None,
    ) -> MergeRequest:
        """
        Enqueues a merge-ready task into the sequential merge queue.
        Maintains deterministic ordering: higher priority first, then FIFO by enqueued_at.
        """
        if isinstance(task, MergeRequest):
            for req in self._queue:
                if req.task_id == task.task_id:
                    return req
            self._queue.append(task)
            self._queue.sort(key=lambda r: (-r.priority, r.enqueued_at))
            return task

        branch = branch_name or getattr(task, "branch_name", None) or f"ao/{task.task_id}"
        path = workspace_path or getattr(task, "workspace_path", None) or f".worktrees/{task.task_id}"
        prio = priority if priority is not None else getattr(task, "priority", 0.0)

        # Check if already queued
        for req in self._queue:
            if req.task_id == task.task_id:
                return req

        request = MergeRequest(
            task_id=task.task_id,
            worker_id=worker_id,
            branch_name=branch,
            workspace_path=path,
            write_set=set(getattr(task, "write_set", set())),
            enqueued_at=time.time(),
            priority=prio,
            status=MergeStatus.PENDING,
            task=task,
        )

        self._queue.append(request)

        # Deterministic sort: FIFO by enqueued_at ascending, task_id ascending
        self._queue.sort(key=lambda r: (r.enqueued_at, r.task_id))


        if self._event_emitter:
            self._event_emitter(
                EventType.MERGE_READY,
                task_id=task.task_id,
                payload={
                    "worker_id": worker_id,
                    "branch_name": branch,
                    "priority": prio,
                    "queue_position": self._queue.index(request) + 1,
                }
            )

        return request

    submit = enqueue

    def process_next(self) -> Optional[MergeResult]:
        """
        Processes the next pending merge request in the queue.
        Strictly enforces serial integration: only one merge attempt executes at a time.
        """
        if self._is_merging or not self._queue:
            return None

        self._is_merging = True
        req = self._queue.pop(0)
        req.status = MergeStatus.MERGING
        self._active_request = req

        if self._event_emitter:
            self._event_emitter(
                EventType.MERGE_STARTED,
                task_id=req.task_id,
                payload={
                    "branch_name": req.branch_name,
                    "target_branch": self._integration_branch,
                    "worker_id": req.worker_id,
                }
            )

        try:
            res = self._adapter.attempt_merge(
                source_branch=req.branch_name,
                target_branch=self._integration_branch,
                task_id=req.task_id,
            )

            if res.success:
                req.status = MergeStatus.MERGED
                req.commit_id = res.commit_id

                # Direct task transition to MERGED
                if req.task is not None and getattr(req.task, "status", None) != TaskState.MERGED:
                    if req.task.status != TaskState.PASSED:
                        req.task.status = TaskState.PASSED
                    req.task.transition_to(TaskState.MERGED, reason="Clean automated integration")

                # 1. Clean up worktree from host filesystem
                if self._worktree_adapter is not None:
                    try:
                        self._worktree_adapter.cleanup_workspace(req.workspace_path, req.branch_name)
                    except Exception:
                        pass

                # 2. Release workspace ownership cleanly
                if self._workspace_registry is not None and self._workspace_registry.has_active_ownership(req.task_id):
                    self._workspace_registry.release(
                        task_id=req.task_id,
                        state=WorkspaceReleaseState.MERGED,
                        reason="Clean automated integration"
                    )
                    if self._event_emitter:
                        self._event_emitter(
                            EventType.WORKSPACE_RELEASED,
                            task_id=req.task_id,
                            payload={
                                "state": WorkspaceReleaseState.MERGED.value,
                                "reason": "Clean automated integration",
                            }
                        )

                # 3. Emit MERGE_COMPLETED event
                if self._event_emitter:
                    self._event_emitter(
                        EventType.MERGE_COMPLETED,
                        task_id=req.task_id,
                        payload={
                            "branch_name": req.branch_name,
                            "commit_id": res.commit_id,
                            "duration": res.duration,
                        }
                    )

                # 4. Notify completion callback (transitions task state & unlocks dependents)
                if self._on_merge_completed:
                    self._on_merge_completed(req.task_id, res)

            else:
                # Merge Conflict or Integration Error
                req.status = MergeStatus.CONFLICT if res.conflict_files else MergeStatus.FAILED
                req.conflict_files = list(res.conflict_files)
                req.error = res.error

                # Release logical ownership lock so future tasks are not blocked indefinitely,
                # but preserve the physical worktree directory on disk for evidence / repair
                if self._workspace_registry is not None and self._workspace_registry.has_active_ownership(req.task_id):
                    self._workspace_registry.release(
                        task_id=req.task_id,
                        state=WorkspaceReleaseState.FAILED,
                        reason=f"Merge failed: {res.error}"
                    )
                    if self._event_emitter:
                        self._event_emitter(
                            EventType.WORKSPACE_RELEASED,
                            task_id=req.task_id,
                            payload={
                                "state": WorkspaceReleaseState.FAILED.value,
                                "reason": f"Merge failed: {res.error}",
                            }
                        )

                # Emit MERGE_FAILED event
                if self._event_emitter:
                    self._event_emitter(
                        EventType.MERGE_FAILED,
                        task_id=req.task_id,
                        payload={
                            "branch_name": req.branch_name,
                            "conflict_files": res.conflict_files,
                            "error": res.error,
                            "duration": res.duration,
                        }
                    )

                # Notify failure callback
                if self._on_merge_failed:
                    self._on_merge_failed(req.task_id, res)

            self._results[req.task_id] = res
            self._history.append(req)
            return res

        finally:
            self._active_request = None
            self._is_merging = False

    def process_all(self) -> List[MergeResult]:
        """
        Continuously drains and processes all requests sequentially until queue is empty.
        Returns all produced MergeResults.
        """
        results: List[MergeResult] = []
        while self.pending_count > 0:
            res = self.process_next()
            if res is not None:
                results.append(res)
            else:
                break
        return results

    def clear(self) -> None:
        """Clears queue state."""
        self._queue.clear()
        self._active_request = None
        self._history.clear()
        self._results.clear()
        self._is_merging = False
