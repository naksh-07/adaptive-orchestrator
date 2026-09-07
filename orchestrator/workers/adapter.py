"""
Adaptive Orchestrator v5 - Execution Adapter & Worker Wake Boundary.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from orchestrator.models import Task
from orchestrator.workers.models import Worker


@dataclass
class ExecutionResult:
    """
    Structured outcome of a worker task execution.
    """
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration: float = 0.0
    is_reuse: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "duration": self.duration,
            "is_reuse": self.is_reuse,
        }


class ExecutionAdapter(ABC):
    """
    Abstract Execution Adapter separating scheduling from actual process/subagent invocation.
    Antigravity process spawning and send_message wake mechanics live behind this interface.
    """

    @abstractmethod
    def dispatch(self, worker: Worker, task: Task) -> ExecutionResult:
        """
        Dispatches an assigned task to the designated worker.
        Returns the ExecutionResult upon task completion.
        """
        raise NotImplementedError

    def is_reuse(self, worker: Worker) -> bool:
        """
        Determines whether dispatching to this worker is a wake/reuse operation
        or an initial spawn.
        """
        return len(worker.task_history) > 0 or worker.metrics.tasks_completed > 0


class MockExecutionAdapter(ExecutionAdapter):
    """
    Deterministic Mock Execution Adapter for Phase 2 testing and simulations.
    Records all invocations, tracks worker reuse vs fresh spawn, and allows configurable outcomes.
    """

    def __init__(
        self,
        default_success: bool = True,
        default_result: Optional[Dict[str, Any]] = None,
        simulate_duration: float = 0.0,
    ) -> None:
        self.default_success = default_success
        self.default_result = default_result or {}
        self.simulate_duration = simulate_duration

        # Configuration overrides
        self.task_results: Dict[str, ExecutionResult] = {}
        self.fail_tasks: Set[str] = set()

        # Telemetry & call records
        self.dispatches: List[Dict[str, Any]] = []
        self.spawn_count: int = 0
        self.reuse_count: int = 0

    def dispatch(self, worker: Worker, task: Task) -> ExecutionResult:
        """
        Executes mock dispatch deterministically.
        """
        reused = self.is_reuse(worker)
        if reused:
            self.reuse_count += 1
        else:
            self.spawn_count += 1

        record = {
            "worker_id": worker.worker_id,
            "task_id": task.task_id,
            "domain": worker.domain,
            "is_reuse": reused,
            "timestamp": time.time(),
        }
        self.dispatches.append(record)

        # Check explicit overrides
        if task.task_id in self.task_results:
            res = self.task_results[task.task_id]
            res.is_reuse = reused
            return res

        if task.task_id in self.fail_tasks or not self.default_success:
            return ExecutionResult(
                success=False,
                error=f"Mock failure on task '{task.task_id}'",
                duration=self.simulate_duration,
                is_reuse=reused,
            )

        return ExecutionResult(
            success=True,
            result=dict(self.default_result),
            duration=self.simulate_duration,
            is_reuse=reused,
        )


class LocalExecutionAdapter(ExecutionAdapter):
    """
    Execution adapter that invokes a Python callable for each dispatched task.
    Enables local in-process testing or custom simulation handlers.
    """

    def __init__(
        self,
        handler: Callable[[Worker, Task], ExecutionResult]
    ) -> None:
        self._handler = handler
        self.dispatches: List[Dict[str, Any]] = []

    def dispatch(self, worker: Worker, task: Task) -> ExecutionResult:
        reused = self.is_reuse(worker)
        self.dispatches.append({
            "worker_id": worker.worker_id,
            "task_id": task.task_id,
            "is_reuse": reused,
            "timestamp": time.time(),
        })

        result = self._handler(worker, task)
        result.is_reuse = reused
        return result
