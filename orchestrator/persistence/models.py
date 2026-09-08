"""
Adaptive Orchestrator v5 - Persistence & Checkpoint Models.
Defines crash-safe, serializable representations of mission states, tasks, and recovery metadata.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CheckpointTrigger(str, Enum):
    """Triggers that can generate a mission checkpoint."""
    MISSION_STATE_CHANGED = "MISSION_STATE_CHANGED"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    REPAIR_RESULT = "REPAIR_RESULT"
    MERGE_RESULT = "MERGE_RESULT"
    WORKER_FAILED = "WORKER_FAILED"
    EXPLICIT = "EXPLICIT"


@dataclass
class SerializedMissionState:
    """
    Complete serializable snapshot of mission execution.
    Contains no transient live handles (sockets, thread locks, process handles).
    """
    mission: Dict[str, Any]
    tasks: Any
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    workers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    trigger: str = CheckpointTrigger.EXPLICIT.value
    checkpoint_id: str = field(default_factory=lambda: f"chk_{uuid.uuid4().hex[:8]}")
    version: str = "5.0"

    @property
    def mission_id(self) -> str:
        return self.mission.get("mission_id", "")

    @property
    def title(self) -> str:
        return self.mission.get("title", "")

    @property
    def mission_state(self) -> str:
        return self.mission.get("state", "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "checkpoint_id": self.checkpoint_id,
            "trigger": self.trigger,
            "timestamp": self.timestamp,
            "mission": self.mission,
            "tasks": self.tasks,
            "dependencies": self.dependencies,
            "workers": self.workers,
            "events": self.events,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SerializedMissionState:
        if not isinstance(data, dict):
            raise ValueError("Serialized mission state must be a JSON object dictionary.")
        if "mission" not in data or "tasks" not in data:
            raise ValueError("Corrupted mission state: missing required 'mission' or 'tasks' key.")

        raw_tasks = data.get("tasks", [])
        if isinstance(raw_tasks, list):
            tasks_val = {t.get("task_id", t.get("id", "")): t for t in raw_tasks}
        else:
            tasks_val = dict(raw_tasks)

        raw_workers = data.get("workers", {})
        if isinstance(raw_workers, list):
            workers_val = {w.get("worker_id", ""): w for w in raw_workers if isinstance(w, dict)}
        elif isinstance(raw_workers, dict):
            workers_val = dict(raw_workers)
        else:
            workers_val = {}

        return cls(
            mission=dict(data.get("mission", {})),
            tasks=tasks_val,
            dependencies=dict(data.get("dependencies", {})),
            workers=workers_val,
            events=list(data.get("events", [])),
            metadata=dict(data.get("metadata", {})),
            timestamp=float(data.get("timestamp", time.time())),
            trigger=str(data.get("trigger", CheckpointTrigger.EXPLICIT.value)),
            checkpoint_id=str(data.get("checkpoint_id", f"chk_{uuid.uuid4().hex[:8]}")),
            version=str(data.get("version", "5.0")),
        )


@dataclass
class InterruptedTaskRecovery:
    """Record of an individual task restored from an interrupted execution state."""
    task_id: str
    prior_state: str
    recovered_state: str
    reason: str

    @property
    def recovered_to(self) -> str:
        return self.recovered_state

    @property
    def previous_state(self) -> str:
        return self.prior_state

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prior_state": self.prior_state,
            "recovered_state": self.recovered_state,
            "reason": self.reason,
        }


@dataclass
class RecoveryReport:
    """Summary of a mission restoration and crash recovery procedure."""
    mission_id: str
    recovered_tasks_count: int
    interrupted_tasks: List[InterruptedTaskRecovery] = field(default_factory=list)
    active_locks_cleared: int = 0
    ready_queue_size: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "recovered_tasks_count": self.recovered_tasks_count,
            "interrupted_tasks": [t.to_dict() for t in self.interrupted_tasks],
            "active_locks_cleared": self.active_locks_cleared,
            "ready_queue_size": self.ready_queue_size,
            "timestamp": self.timestamp,
        }
