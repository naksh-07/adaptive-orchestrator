"""
Adaptive Orchestrator v5 - Integration Manager Facade.
Coordinates worktree finalization, merge queue submission, and serialized branch integration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from orchestrator.integration.adapter import MergeAdapter, MockMergeAdapter
from orchestrator.integration.models import MergeRequest, MergeResult
from orchestrator.integration.queue import MergeQueue
from orchestrator.models import Event, EventType

if TYPE_CHECKING:
    from orchestrator.models import Task
    from orchestrator.workspace.adapter import WorktreeAdapter
    from orchestrator.workspace.registry import WorkspaceRegistry


class IntegrationManager:
    """
    High-level integration coordinator.
    Manages the sequential merge queue, worktree adapter, and post-merge hooks.
    """

    def __init__(
        self,
        merge_adapter: Optional[MergeAdapter] = None,
        adapter: Optional[MergeAdapter] = None,
        workspace_registry: Optional[WorkspaceRegistry] = None,
        worktree_adapter: Optional[WorktreeAdapter] = None,
        integration_branch: str = "ao/integration",
        event_emitter: Optional[Callable[[EventType, Optional[str], Optional[Dict[str, Any]]], Event]] = None,
        on_merge_completed: Optional[Callable[[str, MergeResult], None]] = None,
        on_merge_failed: Optional[Callable[[str, MergeResult], None]] = None,
    ) -> None:
        eff_adapter = adapter or merge_adapter or MockMergeAdapter()
        self._queue = MergeQueue(
            adapter=eff_adapter,
            workspace_registry=workspace_registry,
            worktree_adapter=worktree_adapter,
            integration_branch=integration_branch,
            event_emitter=event_emitter,
            on_merge_completed=on_merge_completed,
            on_merge_failed=on_merge_failed,
        )

    @property
    def queue(self) -> MergeQueue:
        return self._queue

    @property
    def merge_queue(self) -> MergeQueue:
        return self._queue

    @property
    def queue_length(self) -> int:
        return len(self._queue._queue)

    @property
    def merge_adapter(self) -> MergeAdapter:
        return self._queue.adapter

    def enqueue(self, request_or_task: Any, *args: Any, **kwargs: Any) -> MergeRequest:
        """Enqueues a task or MergeRequest into the merge queue."""
        return self._queue.enqueue(request_or_task, *args, **kwargs)

    def submit_for_merge(
        self,
        task: Task,
        worker_id: str,
        branch_name: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ) -> MergeRequest:
        """Enqueues task into the sequential merge queue."""
        return self._queue.enqueue(
            task=task,
            worker_id=worker_id,
            branch_name=branch_name,
            workspace_path=workspace_path,
        )

    def process_pending_merges(self) -> List[MergeResult]:
        """Processes all pending merges sequentially."""
        return self._queue.process_all()

    def process_next_merge(self) -> Optional[MergeResult]:
        """Processes next single pending merge."""
        return self._queue.process_next()

    def process_next(self) -> Optional[MergeResult]:
        """Alias for process_next_merge."""
        return self._queue.process_next()
