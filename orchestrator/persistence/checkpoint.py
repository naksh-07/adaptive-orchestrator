"""
Adaptive Orchestrator v5 - Checkpoint Policy.
Governs sensible checkpoint triggers to prevent excessive I/O while guaranteeing crash safety.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Set

from orchestrator.models import Event, EventType
from orchestrator.persistence.models import CheckpointTrigger


@dataclass
class CheckpointPolicy:
    """
    Configurable checkpoint policy.
    Determines which engine lifecycle events trigger atomic state persistence.
    """
    enabled: bool = True
    triggers: Set[CheckpointTrigger] = field(default_factory=set)
    checkpoint_file: str = "mission_dag.json"
    min_interval_seconds: float = 0.0

    def __init__(
        self,
        enabled: bool = True,
        triggers: Optional[Set[CheckpointTrigger]] = None,
        enabled_triggers: Optional[Set[CheckpointTrigger]] = None,
        checkpoint_file: str = "mission_dag.json",
        min_interval_seconds: float = 0.0,
        throttle_seconds: Optional[float] = None,
    ) -> None:
        self.enabled = enabled
        self.triggers = enabled_triggers if enabled_triggers is not None else (
            triggers if triggers is not None else {
                CheckpointTrigger.MISSION_STATE_CHANGED,
                CheckpointTrigger.TASK_ASSIGNED,
                CheckpointTrigger.TASK_COMPLETED,
                CheckpointTrigger.TASK_FAILED,
                CheckpointTrigger.VERIFICATION_RESULT,
                CheckpointTrigger.REPAIR_RESULT,
                CheckpointTrigger.MERGE_RESULT,
                CheckpointTrigger.WORKER_FAILED,
                CheckpointTrigger.EXPLICIT,
            }
        )
        self.checkpoint_file = checkpoint_file
        self.min_interval_seconds = throttle_seconds if throttle_seconds is not None else min_interval_seconds
        self._last_checkpoint_time: float = 0.0

    @property
    def throttle_seconds(self) -> float:
        return self.min_interval_seconds

    @property
    def enabled_triggers(self) -> Set[CheckpointTrigger]:
        return self.triggers

    @enabled_triggers.setter
    def enabled_triggers(self, val: Set[CheckpointTrigger]) -> None:
        self.triggers = val

    def should_checkpoint_event(self, event_or_str: Any) -> bool:
        """Evaluates whether an event name/type matches configured checkpoint triggers."""
        ev_str = event_or_str.value if hasattr(event_or_str, "value") else str(event_or_str)
        # Check against triggers
        for trig in self.triggers:
            if trig.value == ev_str or trig.name == ev_str:
                return self.should_checkpoint(trig)
        return False

    def should_checkpoint(self, trigger: CheckpointTrigger) -> bool:
        """Evaluates whether an atomic checkpoint should be executed for the trigger."""
        if not self.enabled:
            return False
        if trigger not in self.triggers:
            return False

        now = time.time()
        if self.min_interval_seconds > 0.0 and trigger != CheckpointTrigger.EXPLICIT:
            if (now - self._last_checkpoint_time) < self.min_interval_seconds:
                return False

        self._last_checkpoint_time = now
        return True

    def map_event_to_trigger(self, event: Event) -> Optional[CheckpointTrigger]:
        """Maps an internal engine EventType to a CheckpointTrigger."""
        mapping = {
            EventType.MISSION_STATE_CHANGED: CheckpointTrigger.MISSION_STATE_CHANGED,
            EventType.TASK_ASSIGNED: CheckpointTrigger.TASK_ASSIGNED,
            EventType.TASK_COMPLETED: CheckpointTrigger.TASK_COMPLETED,
            EventType.TASK_FAILED: CheckpointTrigger.TASK_FAILED,
            EventType.VERIFICATION_PASSED: CheckpointTrigger.VERIFICATION_RESULT,
            EventType.VERIFICATION_FAILED: CheckpointTrigger.VERIFICATION_RESULT,
            EventType.REPAIR_COMPLETED: CheckpointTrigger.REPAIR_RESULT,
            EventType.REPAIR_FAILED: CheckpointTrigger.REPAIR_RESULT,
            EventType.MERGE_COMPLETED: CheckpointTrigger.MERGE_RESULT,
            EventType.MERGE_FAILED: CheckpointTrigger.MERGE_RESULT,
            EventType.WORKER_FAILED: CheckpointTrigger.WORKER_FAILED,
        }
        return mapping.get(event.event_type)
