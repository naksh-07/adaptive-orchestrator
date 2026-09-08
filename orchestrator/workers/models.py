"""
Adaptive Orchestrator v5 - Worker Domain Models and State Machine.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from orchestrator.exceptions import InvalidWorkerStateError


class WorkerState(str, Enum):
    """
    Lifecycle states for an autonomous logical agent worker.
    """
    IDLE = "IDLE"
    BUSY = "BUSY"
    FAILED = "FAILED"
    RETIRED = "RETIRED"
    DEGRADED = "FAILED"
    OFFLINE = "FAILED"

    @property
    def is_available(self) -> bool:
        """Indicates whether the worker is currently available to accept tasks."""
        return self == WorkerState.IDLE

    @property
    def is_terminal(self) -> bool:
        """Indicates whether the worker is in a terminal retired state."""
        return self == WorkerState.RETIRED


VALID_WORKER_TRANSITIONS: Dict[WorkerState, Set[WorkerState]] = {
    WorkerState.IDLE: {
        WorkerState.BUSY,
        WorkerState.FAILED,
        WorkerState.RETIRED,
    },
    WorkerState.BUSY: {
        WorkerState.IDLE,
        WorkerState.FAILED,
        WorkerState.RETIRED,
    },
    WorkerState.FAILED: {
        WorkerState.IDLE,     # Worker reset / recovered
        WorkerState.RETIRED,
    },
    WorkerState.RETIRED: set(),
}


@dataclass
class WorkerMetrics:
    """
    Operational metrics and telemetry for a worker.
    """
    tasks_completed: int = 0
    tasks_failed: int = 0
    consecutive_failures: int = 0
    total_execution_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "consecutive_failures": self.consecutive_failures,
            "total_execution_time": self.total_execution_time,
        }


@dataclass
class Worker:
    """
    Canonical Worker entity representing a reusable logical agent worker.
    Tracks both internal orchestration state and Antigravity native runtime identity.
    """
    worker_id: str
    domain: str = "general"
    supported_domains: Any = field(default_factory=set)
    max_concurrency: int = 1
    state: WorkerState = WorkerState.IDLE
    current_task_id: Optional[str] = None
    task_history: List[str] = field(default_factory=list)
    metrics: WorkerMetrics = field(default_factory=WorkerMetrics)
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    conversation_id: Optional[str] = None
    native_agent_name: Optional[str] = None
    native_session_id: Optional[str] = None
    model_tier: Optional[str] = None
    resolved_model: Optional[str] = None
    workspace_mode: Optional[str] = None
    branch_name: Optional[str] = None
    workspace_path: Optional[str] = None
    is_native: bool = False
    is_reusable: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.supported_domains, list):
            self.supported_domains = set(self.supported_domains)
        elif not isinstance(self.supported_domains, set):
            self.supported_domains = set(self.supported_domains) if self.supported_domains else set()
        if not self.supported_domains and self.domain:
            self.supported_domains = {self.domain}
        # Synchronize conversation_id and native_session_id
        if self.conversation_id and not self.native_session_id:
            self.native_session_id = self.conversation_id
        elif self.native_session_id and not self.conversation_id:
            self.conversation_id = self.native_session_id

    def mark_failed(self, reason: str = "") -> None:
        """Transitions worker to FAILED state."""
        self.transition_to(WorkerState.FAILED, reason=reason)

    def supports_domain(self, domain: str) -> bool:
        """Checks if worker supports a specific domain."""
        if domain == "general" or "general" in self.supported_domains:
            return True
        return domain in self.supported_domains or self.domain == domain

    @property
    def tasks_completed(self) -> int:
        """Returns total tasks completed by this worker."""
        return self.metrics.tasks_completed

    @property
    def tasks_failed(self) -> int:
        """Returns total tasks failed by this worker."""
        return self.metrics.tasks_failed

    @property
    def is_idle(self) -> bool:
        """Returns True if the worker is IDLE."""
        return self.state == WorkerState.IDLE

    @property
    def is_busy(self) -> bool:
        """Returns True if the worker is currently executing a task."""
        return self.state == WorkerState.BUSY

    @property
    def is_available(self) -> bool:
        """Returns True if the worker is ready to receive new work."""
        return self.is_idle and self.current_task_id is None

    def can_transition_to(self, target_state: WorkerState) -> bool:
        """Returns True if the worker can transition to the target state."""
        return target_state in VALID_WORKER_TRANSITIONS.get(self.state, set())

    def transition_to(self, target_state: WorkerState, reason: str = "") -> None:
        """
        Transitions worker to target state if valid.
        Raises InvalidWorkerStateError if forbidden.
        """
        if target_state == self.state:
            return

        if not self.can_transition_to(target_state):
            raise InvalidWorkerStateError(
                f"Cannot transition worker '{self.worker_id}' from {self.state.value} to {target_state.value}"
                + (f" ({reason})" if reason else "")
            )

        self.state = target_state
        self.last_active_at = time.time()

    def assign_task(self, task_id: str) -> None:
        """
        Assigns task_id to the worker and transitions state to BUSY.
        """
        if not self.is_available:
            raise InvalidWorkerStateError(
                f"Cannot assign task '{task_id}' to worker '{self.worker_id}': state is {self.state.value}, current_task={self.current_task_id}"
            )
        self.transition_to(WorkerState.BUSY, reason=f"Assigned task {task_id}")
        self.current_task_id = task_id
        self.last_active_at = time.time()

    def wake(self, task_id: str) -> None:
        """
        Wakes a warm idle worker and assigns the task.
        Alias for assign_task implementing the native wake concept.
        """
        self.assign_task(task_id)

    def complete_task(self, task_id: str, duration: float = 0.0) -> None:
        """
        Marks current task execution complete, returns worker to IDLE, and updates metrics.
        """
        if self.current_task_id != task_id:
            raise InvalidWorkerStateError(
                f"Cannot complete task '{task_id}' on worker '{self.worker_id}': currently executing '{self.current_task_id}'"
            )

        self.task_history.append(task_id)
        self.current_task_id = None
        self.metrics.tasks_completed += 1
        self.metrics.consecutive_failures = 0
        self.metrics.total_execution_time += max(0.0, duration)
        self.transition_to(WorkerState.IDLE, reason=f"Completed task {task_id}")
        self.last_active_at = time.time()

    def fail_task(
        self,
        task_id: str,
        error: str = "",
        duration: float = 0.0,
        worker_failed: bool = False
    ) -> None:
        """
        Records a task execution failure on the worker.
        If worker_failed is True (e.g. process crash), worker transitions to FAILED.
        Otherwise, worker returns to IDLE for future tasks.
        """
        if self.current_task_id != task_id and self.current_task_id is not None:
            raise InvalidWorkerStateError(
                f"Cannot fail task '{task_id}' on worker '{self.worker_id}': currently executing '{self.current_task_id}'"
            )

        if self.current_task_id:
            self.task_history.append(task_id)
            self.current_task_id = None

        self.metrics.tasks_failed += 1
        self.metrics.consecutive_failures += 1
        self.metrics.total_execution_time += max(0.0, duration)

        target_state = WorkerState.FAILED if worker_failed else WorkerState.IDLE
        self.transition_to(target_state, reason=f"Failed task {task_id}: {error}")
        self.last_active_at = time.time()

    def retire(self, reason: str = "") -> None:
        """Retires the worker permanently."""
        self.transition_to(WorkerState.RETIRED, reason=reason)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes worker to a dictionary representation."""
        return {
            "worker_id": self.worker_id,
            "domain": self.domain,
            "supported_domains": list(self.supported_domains),
            "max_concurrency": self.max_concurrency,
            "state": self.state.value,
            "current_task_id": self.current_task_id,
            "task_history": list(self.task_history),
            "metrics": self.metrics.to_dict(),
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "conversation_id": self.conversation_id,
            "native_agent_name": self.native_agent_name,
            "native_session_id": self.native_session_id or self.conversation_id,
            "model_tier": self.model_tier,
            "resolved_model": self.resolved_model,
            "workspace_mode": self.workspace_mode,
            "branch_name": self.branch_name,
            "workspace_path": self.workspace_path,
            "is_native": self.is_native,
            "is_reusable": self.is_reusable,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Worker:
        """Constructs a Worker from serialized dictionary representation."""
        metrics_data = data.get("metrics", {})
        metrics = WorkerMetrics(
            tasks_completed=metrics_data.get("tasks_completed", 0),
            tasks_failed=metrics_data.get("tasks_failed", 0),
            consecutive_failures=metrics_data.get("consecutive_failures", 0),
            total_execution_time=metrics_data.get("total_execution_time", 0.0),
        )
        state_str = data.get("state", WorkerState.IDLE.value)
        try:
            state = WorkerState(state_str)
        except ValueError:
            state = WorkerState.IDLE

        cid = data.get("conversation_id") or data.get("native_session_id")

        return cls(
            worker_id=data["worker_id"],
            domain=data.get("domain", "general"),
            supported_domains=set(data.get("supported_domains", [])),
            max_concurrency=data.get("max_concurrency", 1),
            state=state,
            current_task_id=data.get("current_task_id"),
            task_history=list(data.get("task_history", [])),
            metrics=metrics,
            created_at=float(data.get("created_at", time.time())),
            last_active_at=float(data.get("last_active_at", time.time())),
            conversation_id=cid,
            native_agent_name=data.get("native_agent_name"),
            native_session_id=cid,
            model_tier=data.get("model_tier"),
            resolved_model=data.get("resolved_model"),
            workspace_mode=data.get("workspace_mode"),
            branch_name=data.get("branch_name"),
            workspace_path=data.get("workspace_path"),
            is_native=bool(data.get("is_native", False)),
            is_reusable=bool(data.get("is_reusable", True)),
            metadata=dict(data.get("metadata", {})),
        )
