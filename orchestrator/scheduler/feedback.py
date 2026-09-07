"""
Adaptive Orchestrator v5 - Execution Feedback Model.
Lightweight, sliding-window runtime signals for adaptive concurrency and health monitoring.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class FeedbackSignalType(str, Enum):
    """
    Canonical execution feedback signals used by the AIMD controller.
    """
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    RATE_LIMIT_ERROR = "RATE_LIMIT_ERROR"      # HTTP 429 / quota exhaustion
    LATENCY_SPIKE = "LATENCY_SPIKE"            # Severe execution latency spike
    WORKER_FAILED = "WORKER_FAILED"            # Worker crash / communication drop
    QUEUE_STARVATION = "QUEUE_STARVATION"      # Ready queue empty despite capacity
    BACKLOG_SATURATION = "BACKLOG_SATURATION"  # Ready queue depth significantly exceeds capacity


@dataclass
class FeedbackSignal:
    """
    A discrete runtime execution signal.
    Compact, deterministic data structure.
    """
    signal_type: FeedbackSignalType
    task_id: Optional[str] = None
    worker_id: Optional[str] = None
    latency: float = 0.0
    error: Optional[str] = None
    is_capacity_error: bool = False
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_type": self.signal_type.value,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "latency": self.latency,
            "error": self.error,
            "is_capacity_error": self.is_capacity_error,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


class FeedbackCollector:
    """
    Lightweight runtime feedback aggregator.
    Tracks execution health, latency history, failure streaks, and capacity pressure.
    Maintains a rolling window without requiring a heavy telemetry database.
    """

    def __init__(self, window_size: int = 50) -> None:
        self._window_size = window_size
        self._signals: List[FeedbackSignal] = []

        # Real-time state metrics
        self._consecutive_successes: int = 0
        self._consecutive_failures: int = 0
        self._total_completed: int = 0
        self._total_failed: int = 0
        self._total_rate_limits: int = 0
        self._total_latency: float = 0.0

    @property
    def consecutive_successes(self) -> int:
        return self._consecutive_successes

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def total_completed(self) -> int:
        return self._total_completed

    @property
    def total_failed(self) -> int:
        return self._total_failed

    @property
    def total_rate_limits(self) -> int:
        return self._total_rate_limits

    @property
    def average_latency(self) -> float:
        if self._total_completed == 0:
            return 0.0
        return self._total_latency / self._total_completed

    def record_signal(self, signal: FeedbackSignal) -> None:
        """
        Ingests a new feedback signal into the sliding window and updates metrics.
        """
        self._signals.append(signal)
        if len(self._signals) > self._window_size:
            self._signals.pop(0)

        if signal.signal_type == FeedbackSignalType.TASK_COMPLETED:
            self._consecutive_successes += 1
            self._consecutive_failures = 0
            self._total_completed += 1
            self._total_latency += max(0.0, signal.latency)

        elif signal.signal_type in (FeedbackSignalType.TASK_FAILED, FeedbackSignalType.WORKER_FAILED):
            self._consecutive_failures += 1
            self._consecutive_successes = 0
            self._total_failed += 1

            if signal.is_capacity_error:
                self._total_rate_limits += 1

        elif signal.signal_type == FeedbackSignalType.RATE_LIMIT_ERROR:
            self._consecutive_failures += 1
            self._consecutive_successes = 0
            self._total_rate_limits += 1

    def record_task_started(self, task_id: str, worker_id: str) -> FeedbackSignal:
        sig = FeedbackSignal(
            signal_type=FeedbackSignalType.TASK_STARTED,
            task_id=task_id,
            worker_id=worker_id,
        )
        self.record_signal(sig)
        return sig

    def record_task_completed(
        self,
        task_id: str,
        worker_id: str,
        latency: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FeedbackSignal:
        sig = FeedbackSignal(
            signal_type=FeedbackSignalType.TASK_COMPLETED,
            task_id=task_id,
            worker_id=worker_id,
            latency=latency,
            metadata=metadata or {},
        )
        self.record_signal(sig)
        return sig

    def record_task_failed(
        self,
        task_id: str,
        worker_id: Optional[str] = None,
        error: str = "",
        is_capacity_error: bool = False,
        latency: float = 0.0,
        worker_failed: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FeedbackSignal:
        sig_type = (
            FeedbackSignalType.RATE_LIMIT_ERROR
            if is_capacity_error
            else (FeedbackSignalType.WORKER_FAILED if worker_failed else FeedbackSignalType.TASK_FAILED)
        )
        sig = FeedbackSignal(
            signal_type=sig_type,
            task_id=task_id,
            worker_id=worker_id,
            latency=latency,
            error=error,
            is_capacity_error=is_capacity_error,
            metadata=metadata or {},
        )
        self.record_signal(sig)
        return sig

    def recent_signals(self, limit: int = 10) -> List[FeedbackSignal]:
        """Returns the most recent N feedback signals."""
        return list(self._signals[-limit:])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consecutive_successes": self._consecutive_successes,
            "consecutive_failures": self._consecutive_failures,
            "total_completed": self._total_completed,
            "total_failed": self._total_failed,
            "total_rate_limits": self._total_rate_limits,
            "average_latency": self.average_latency,
            "signals_in_window": len(self._signals),
        }
