"""
Adaptive Orchestrator v5 - Additive Increase / Multiplicative Decrease (AIMD) Controller.
Controls physical active concurrency dynamically based on runtime execution feedback.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from orchestrator.scheduler.feedback import FeedbackCollector, FeedbackSignal, FeedbackSignalType


@dataclass(frozen=True)
class AIMDConfig:
    """
    Configuration parameters for the AIMD concurrency controller.
    All bounds and multipliers are explicitly configurable with sensible defaults.
    """
    min_capacity: int = 2
    max_capacity: int = 8
    initial_capacity: int = 4
    increase_step: int = 1
    decrease_factor: float = 0.5
    healthy_threshold: int = 3
    cooldown_steps: int = 1

    def __post_init__(self) -> None:
        if self.min_capacity < 1:
            raise ValueError(f"min_capacity must be >= 1, got {self.min_capacity}")
        if self.max_capacity < self.min_capacity:
            raise ValueError(
                f"max_capacity ({self.max_capacity}) cannot be less than min_capacity ({self.min_capacity})"
            )
        if not (self.min_capacity <= self.initial_capacity <= self.max_capacity):
            raise ValueError(
                f"initial_capacity ({self.initial_capacity}) must be between min ({self.min_capacity}) and max ({self.max_capacity})"
            )
        if self.increase_step < 1:
            raise ValueError(f"increase_step must be >= 1, got {self.increase_step}")
        if not (0.0 < self.decrease_factor < 1.0):
            raise ValueError(f"decrease_factor must be between 0.0 and 1.0, got {self.decrease_factor}")
        if self.healthy_threshold < 1:
            raise ValueError(f"healthy_threshold must be >= 1, got {self.healthy_threshold}")
        if self.cooldown_steps < 0:
            raise ValueError(f"cooldown_steps must be >= 0, got {self.cooldown_steps}")


@dataclass
class AIMDState:
    """
    Deterministic snapshot of the AIMD controller's internal state.
    """
    current_capacity: int
    consecutive_successes: int
    consecutive_failures: int
    cooldown_remaining: int
    total_increases: int
    total_decreases: int

    @property
    def in_cooldown(self) -> bool:
        return self.cooldown_remaining > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_capacity": self.current_capacity,
            "consecutive_successes": self.consecutive_successes,
            "consecutive_failures": self.consecutive_failures,
            "cooldown_remaining": self.cooldown_remaining,
            "in_cooldown": self.in_cooldown,
            "total_increases": self.total_increases,
            "total_decreases": self.total_decreases,
        }


class AIMDController:
    """
    Additive Increase / Multiplicative Decrease (AIMD) Concurrency Controller.

    Governs the physical active concurrency capacity allocated to running tasks.
    Separates logical DAG width from physical execution capacity.
    Operates strictly deterministically without hidden background threads or random jitter.
    """

    def __init__(self, config: Optional[AIMDConfig] = None) -> None:
        self._config = config or AIMDConfig()
        self._capacity = self._config.initial_capacity
        self._consecutive_successes = 0
        self._consecutive_failures = 0
        self._cooldown_remaining = 0
        self._total_increases = 0
        self._total_decreases = 0

    @property
    def config(self) -> AIMDConfig:
        return self._config

    @property
    def current_capacity(self) -> int:
        """Current physical worker concurrency capacity."""
        return self._capacity

    @property
    def min_capacity(self) -> int:
        return self._config.min_capacity

    @property
    def max_capacity(self) -> int:
        return self._config.max_capacity

    @property
    def in_cooldown(self) -> bool:
        return self._cooldown_remaining > 0

    @property
    def state(self) -> AIMDState:
        """Returns snapshot of current state."""
        return AIMDState(
            current_capacity=self._capacity,
            consecutive_successes=self._consecutive_successes,
            consecutive_failures=self._consecutive_failures,
            cooldown_remaining=self._cooldown_remaining,
            total_increases=self._total_increases,
            total_decreases=self._total_decreases,
        )

    def can_dispatch(self, active_tasks: int) -> bool:
        """
        Safety capacity gate: returns True only if active tasks are strictly below capacity.
        """
        return active_tasks < self._capacity

    def process_feedback(self, signal: FeedbackSignal) -> int:
        """
        Processes a feedback signal and deterministically updates capacity.
        Returns the updated current capacity.
        """
        if signal.signal_type == FeedbackSignalType.RATE_LIMIT_ERROR or signal.is_capacity_error:
            self._apply_multiplicative_decrease(reason=f"Capacity/rate limit error: {signal.error or '429'}")

        elif signal.signal_type == FeedbackSignalType.TASK_COMPLETED:
            self._consecutive_successes += 1
            self._consecutive_failures = 0

            # Decrement cooldown if active
            if self._cooldown_remaining > 0:
                self._cooldown_remaining -= 1
            elif self._consecutive_successes >= self._config.healthy_threshold:
                self._apply_additive_increase(reason=f"{self._consecutive_successes} consecutive healthy completions")

        elif signal.signal_type in (FeedbackSignalType.TASK_FAILED, FeedbackSignalType.WORKER_FAILED):
            self._consecutive_failures += 1
            self._consecutive_successes = 0

            # Repeated consecutive failures trigger multiplicative decrease as congestion indicator
            if self._consecutive_failures >= self._config.healthy_threshold:
                self._apply_multiplicative_decrease(
                    reason=f"{self._consecutive_failures} consecutive task failures"
                )

        return self._capacity

    def _apply_additive_increase(self, reason: str = "") -> None:
        """
        Increases capacity additively by increase_step, clamped to max_capacity.
        """
        if self._capacity < self._config.max_capacity:
            old_cap = self._capacity
            self._capacity = min(self._config.max_capacity, self._capacity + self._config.increase_step)
            if self._capacity > old_cap:
                self._total_increases += 1
        self._consecutive_successes = 0

    def _apply_multiplicative_decrease(self, reason: str = "") -> None:
        """
        Decreases capacity multiplicatively by decrease_factor, clamped to min_capacity.
        Enters cooldown period to prevent thrashing.
        """
        old_cap = self._capacity
        new_cap = math.floor(self._capacity * self._config.decrease_factor)
        self._capacity = max(self._config.min_capacity, new_cap)

        if self._capacity < old_cap:
            self._total_decreases += 1

        self._consecutive_successes = 0
        self._consecutive_failures = 0
        self._cooldown_remaining = self._config.cooldown_steps

    def force_capacity(self, capacity: int) -> int:
        """
        Directly sets capacity within configured bounds (e.g. for testing or explicit operator override).
        """
        self._capacity = max(self._config.min_capacity, min(self._config.max_capacity, capacity))
        return self._capacity

    def reset(self) -> None:
        """Resets controller state to initial configuration."""
        self._capacity = self._config.initial_capacity
        self._consecutive_successes = 0
        self._consecutive_failures = 0
        self._cooldown_remaining = 0
        self._total_increases = 0
        self._total_decreases = 0
