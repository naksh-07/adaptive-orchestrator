"""
Adaptive Orchestrator v5 - Core Domain Models and State Machines.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Set

from orchestrator.exceptions import InvalidStateTransitionError


class TaskState(str, Enum):
    """
    Canonical Task States for the Adaptive Orchestrator v5 Foundation.
    Separates foundation scheduling states from future physical worker states.
    """
    PENDING = "PENDING"
    BLOCKED = "BLOCKED"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        """Indicates whether this state is terminal under normal execution."""
        return self in (TaskState.PASSED, TaskState.FAILED, TaskState.CANCELLED)

    @property
    def is_success(self) -> bool:
        """Indicates whether the task completed successfully and satisfies dependents."""
        return self == TaskState.PASSED

    @property
    def is_active(self) -> bool:
        """Indicates whether the task is actively executing or undergoing verification."""
        return self in (TaskState.RUNNING, TaskState.VERIFYING, TaskState.RETRYING)


# Explicit Valid State Transitions for Tasks
VALID_TASK_TRANSITIONS: Dict[TaskState, Set[TaskState]] = {
    TaskState.PENDING: {
        TaskState.READY,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
    },
    TaskState.BLOCKED: {
        TaskState.PENDING,
        TaskState.READY,
        TaskState.CANCELLED,
    },
    TaskState.READY: {
        TaskState.RUNNING,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
    },
    TaskState.RUNNING: {
        TaskState.VERIFYING,
        TaskState.PASSED,
        TaskState.FAILED,
        TaskState.RETRYING,
        TaskState.CANCELLED,
    },
    TaskState.VERIFYING: {
        TaskState.PASSED,
        TaskState.FAILED,
        TaskState.RETRYING,
        TaskState.CANCELLED,
    },
    TaskState.RETRYING: {
        TaskState.RUNNING,
        TaskState.READY,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.PASSED: {
        # PASSED is terminal during standard execution; may transition to PENDING on graph invalidation
        TaskState.PENDING,
        TaskState.CANCELLED,
    },
    TaskState.FAILED: {
        # FAILED can be explicitly retried or invalidated
        TaskState.RETRYING,
        TaskState.READY,
        TaskState.PENDING,
        TaskState.CANCELLED,
    },
    TaskState.CANCELLED: set(),
}


@dataclass
class Task:
    """
    Canonical Task entity representing a discrete unit of work in the Mission DAG.
    Minimal foundation model strictly decoupled from physical worker and workspace mechanics.
    """
    task_id: str
    mission_id: str
    title: str
    description: str = ""
    status: TaskState = TaskState.PENDING
    domain: str = "general"
    dependencies: Set[str] = field(default_factory=set)
    dependents: Set[str] = field(default_factory=set)
    priority: float = 0.0
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    retry_count: int = 0
    max_retries: int = 2
    metadata: Dict[str, Any] = field(default_factory=dict)

    def can_transition_to(self, target_state: TaskState) -> bool:
        """Returns True if transition from current status to target_state is permitted."""
        return target_state in VALID_TASK_TRANSITIONS.get(self.status, set())

    def transition_to(self, target_state: TaskState, reason: str = "") -> None:
        """
        Transitions the task to target_state if valid, updating timestamps accordingly.
        Raises InvalidStateTransitionError if the transition is prohibited.
        """
        if target_state == self.status:
            return  # No-op idempotency

        if not self.can_transition_to(target_state):
            raise InvalidStateTransitionError(
                f"Cannot transition task '{self.task_id}' from {self.status.value} to {target_state.value}"
                + (f" ({reason})" if reason else "")
            )

        self.status = target_state
        now = time.time()

        if target_state == TaskState.RUNNING and self.started_at is None:
            self.started_at = now
        elif target_state in (TaskState.PASSED, TaskState.FAILED, TaskState.CANCELLED):
            self.completed_at = now
        elif target_state == TaskState.RETRYING:
            self.retry_count += 1

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the task to a clean dictionary representation."""
        return {
            "task_id": self.task_id,
            "mission_id": self.mission_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "domain": self.domain,
            "dependencies": sorted(list(self.dependencies)),
            "dependents": sorted(list(self.dependents)),
            "priority": self.priority,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "metadata": dict(self.metadata),
        }


class MissionState(str, Enum):
    """Lifecycle states for a Mission."""
    DRAFTING = "DRAFTING"
    PLAN_APPROVED = "PLAN_APPROVED"
    EXECUTING = "EXECUTING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


VALID_MISSION_TRANSITIONS: Dict[MissionState, Set[MissionState]] = {
    MissionState.DRAFTING: {MissionState.PLAN_APPROVED, MissionState.CANCELLED},
    MissionState.PLAN_APPROVED: {MissionState.EXECUTING, MissionState.CANCELLED},
    MissionState.EXECUTING: {MissionState.PAUSED, MissionState.COMPLETED, MissionState.FAILED, MissionState.CANCELLED},
    MissionState.PAUSED: {MissionState.EXECUTING, MissionState.CANCELLED},
    MissionState.COMPLETED: set(),
    MissionState.FAILED: set(),
    MissionState.CANCELLED: set(),
}


@dataclass
class Mission:
    """
    Mission represents one complete user-request execution.
    Minimal foundation model encapsulating high-level execution state and metadata.
    """
    mission_id: str
    title: str
    description: str = ""
    state: MissionState = MissionState.DRAFTING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def transition_to(self, target_state: MissionState, reason: str = "") -> None:
        """Transitions mission state if valid; raises InvalidStateTransitionError otherwise."""
        if target_state == self.state:
            return

        allowed = VALID_MISSION_TRANSITIONS.get(self.state, set())
        if target_state not in allowed:
            raise InvalidStateTransitionError(
                f"Cannot transition mission '{self.mission_id}' from {self.state.value} to {target_state.value}"
                + (f" ({reason})" if reason else "")
            )

        self.state = target_state
        self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the mission to a clean dictionary representation."""
        return {
            "mission_id": self.mission_id,
            "title": self.title,
            "description": self.description,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


class EventType(str, Enum):
    """Core internal event types emitted by the mission and graph engines."""
    MISSION_CREATED = "MISSION_CREATED"
    MISSION_STATE_CHANGED = "MISSION_STATE_CHANGED"
    TASK_CREATED = "TASK_CREATED"
    TASK_READY = "TASK_READY"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_STARTED = "TASK_STARTED"
    TASK_VERIFYING = "TASK_VERIFYING"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_RETRYING = "TASK_RETRYING"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_STATE_CHANGED = "TASK_STATE_CHANGED"
    DEPENDENCY_SATISFIED = "DEPENDENCY_SATISFIED"
    DEPENDENCY_INVALIDATED = "DEPENDENCY_INVALIDATED"
    GRAPH_MUTATED = "GRAPH_MUTATED"
    WORKER_REGISTERED = "WORKER_REGISTERED"
    WORKER_UNREGISTERED = "WORKER_UNREGISTERED"
    WORKER_BUSY = "WORKER_BUSY"
    WORKER_IDLE = "WORKER_IDLE"
    WORKER_RETIRED = "WORKER_RETIRED"
    WORKER_FAILED = "WORKER_FAILED"
    CAPACITY_CHANGED = "CAPACITY_CHANGED"


@dataclass
class Event:
    """
    Lightweight, structured internal event representation.
    No prose narratives; compact, deterministic payload.
    """
    event_type: EventType
    mission_id: str
    task_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the event to a dictionary representation."""
        return {
            "event_type": self.event_type.value,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "payload": self.payload,
        }
