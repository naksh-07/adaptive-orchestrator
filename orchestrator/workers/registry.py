"""
Adaptive Orchestrator v5 - Reusable Worker Registry / Pool.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from orchestrator.exceptions import (
    DuplicateWorkerError,
    WorkerNotFoundError,
)
from orchestrator.workers.models import Worker, WorkerState


class WorkerRegistry:
    """
    Stateful registry and pool for reusable logical workers.
    Responsible for:
      - Registering and unregistering workers
      - Tracking idle, busy, failed, and retired workers
      - Worker-to-task assignment and release
      - Maintaining worker reuse invariants
      - Deterministic snapshots
    """

    def __init__(self) -> None:
        self._workers: Dict[str, Worker] = {}
        self._task_to_worker: Dict[str, str] = {}

    def register_worker(self, worker: Worker) -> None:
        """
        Registers a worker into the pool.
        Raises DuplicateWorkerError if worker_id is already registered.
        """
        if worker.worker_id in self._workers:
            raise DuplicateWorkerError(
                f"Worker with ID '{worker.worker_id}' is already registered."
            )
        self._workers[worker.worker_id] = worker
        if worker.current_task_id:
            self._task_to_worker[worker.current_task_id] = worker.worker_id

    def unregister_worker(self, worker_id: str) -> Worker:
        """
        Removes a worker from the registry.
        Raises WorkerNotFoundError if worker_id does not exist.
        """
        if worker_id not in self._workers:
            raise WorkerNotFoundError(f"Worker '{worker_id}' not found in registry.")

        worker = self._workers.pop(worker_id)
        if worker.current_task_id and worker.current_task_id in self._task_to_worker:
            del self._task_to_worker[worker.current_task_id]
        return worker

    def retire_worker(self, worker_id: str, reason: str = "") -> Worker:
        """
        Transitions a worker to RETIRED state while keeping it in the registry for history/audit.
        Clears any task association.
        """
        worker = self.get_worker(worker_id)
        if worker.current_task_id and worker.current_task_id in self._task_to_worker:
            del self._task_to_worker[worker.current_task_id]
        worker.retire(reason=reason)
        return worker

    def get_worker(self, worker_id: str) -> Worker:
        """
        Retrieves a worker by ID.
        Raises WorkerNotFoundError if not found.
        """
        if worker_id not in self._workers:
            raise WorkerNotFoundError(f"Worker '{worker_id}' not found in registry.")
        return self._workers[worker_id]

    def has_worker(self, worker_id: str) -> bool:
        """Returns True if the worker exists in the registry."""
        return worker_id in self._workers

    def get_idle_workers(self, domain: Optional[str] = None) -> List[Worker]:
        """
        Returns all IDLE workers, sorted deterministically by worker_id.
        Optionally filters by domain.
        """
        idle = [
            w for w in self._workers.values()
            if w.is_idle and (domain is None or w.domain == domain)
        ]
        return sorted(idle, key=lambda w: w.worker_id)

    def get_busy_workers(self) -> List[Worker]:
        """Returns all BUSY workers, sorted deterministically by worker_id."""
        return sorted([w for w in self._workers.values() if w.is_busy], key=lambda w: w.worker_id)

    def get(self, worker_id: str, default: Optional[Worker] = None) -> Optional[Worker]:
        """Convenience method returning worker or default if not found."""
        return self._workers.get(worker_id, default)

    def release(self, worker_id: str, success: bool = True) -> Optional[Worker]:
        """Alias for release_worker, safe no-op if idle or not found."""
        if worker_id not in self._workers:
            return None
        return self.release_worker(worker_id, success=success)

    def list_all(self) -> List[Worker]:
        """Returns list of all registered workers."""
        return list(self._workers.values())

    def get_workers_by_domain(self, domain: str) -> List[Worker]:
        """Returns all workers in the specified domain, sorted deterministically."""
        return sorted(
            [w for w in self._workers.values() if w.domain == domain],
            key=lambda w: w.worker_id,
        )

    def get_worker_for_task(self, task_id: str) -> Optional[Worker]:
        """Returns the worker currently assigned to task_id, or None."""
        worker_id = self._task_to_worker.get(task_id)
        if worker_id and worker_id in self._workers:
            return self._workers[worker_id]
        return None

    def assign_task(self, worker_id: str, task_id: str) -> Worker:
        """
        Assigns task_id to worker_id.
        Transitions worker to BUSY and records association.
        """
        worker = self.get_worker(worker_id)
        worker.assign_task(task_id)
        self._task_to_worker[task_id] = worker_id
        return worker

    def release_worker(
        self,
        worker_id: str,
        success: bool = True,
        duration: float = 0.0,
        error: str = "",
        worker_failed: bool = False
    ) -> Worker:
        """
        Releases worker from its current task.
        Transitions worker back to IDLE (or FAILED if worker_failed is True).
        Removes task association and updates worker metrics.
        """
        worker = self.get_worker(worker_id)
        task_id = worker.current_task_id

        if task_id and task_id in self._task_to_worker:
            del self._task_to_worker[task_id]

        if task_id:
            if success:
                worker.complete_task(task_id=task_id, duration=duration)
            else:
                worker.fail_task(
                    task_id=task_id,
                    error=error,
                    duration=duration,
                    worker_failed=worker_failed
                )

        return worker

    def all_workers(self) -> List[Worker]:
        """Returns all workers in deterministic order by worker_id."""
        return sorted(list(self._workers.values()), key=lambda w: w.worker_id)

    def snapshot(self) -> List[Dict[str, Any]]:
        """Returns deterministic serialized snapshot of all workers."""
        return [w.to_dict() for w in self.all_workers()]

    @property
    def worker_count(self) -> int:
        """Total number of registered workers."""
        return len(self._workers)

    @property
    def idle_count(self) -> int:
        """Number of currently idle workers."""
        return sum(1 for w in self._workers.values() if w.is_idle)

    @property
    def busy_count(self) -> int:
        """Number of currently busy workers."""
        return sum(1 for w in self._workers.values() if w.is_busy)
