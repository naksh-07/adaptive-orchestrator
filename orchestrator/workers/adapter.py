"""
Adaptive Orchestrator v5 - Execution Adapter & Worker Wake Boundary.
Handles execution abstraction, passing tasks and execution profiles to workers.
"""

from __future__ import annotations

import inspect
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from orchestrator.models import Task
from orchestrator.routing.models import ExecutionProfile
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
    is_capacity_error: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "duration": self.duration,
            "is_reuse": self.is_reuse,
            "is_capacity_error": self.is_capacity_error,
        }


class ExecutionAdapter(ABC):
    """
    Abstract Execution Adapter separating scheduling from actual process/subagent invocation.
    Antigravity process spawning and send_message wake mechanics live behind this interface.
    """

    @abstractmethod
    def dispatch(
        self,
        worker: Worker,
        task: Task,
        execution_profile: Optional[ExecutionProfile] = None,
    ) -> ExecutionResult:
        """
        Dispatches an assigned task to the designated worker with an execution profile.
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
    Deterministic Mock Execution Adapter for Phase 2 & 3 testing and simulations.
    Records all invocations, tracks worker reuse vs fresh spawn, routes execution profiles,
    and supports simulating rate limits and capacity errors.
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
        self.rate_limit_tasks: Set[str] = set()

        # Telemetry & call records
        self.dispatches: List[Dict[str, Any]] = []
        self.spawn_count: int = 0
        self.reuse_count: int = 0

    def dispatch(
        self,
        worker: Worker,
        task: Task,
        execution_profile: Optional[ExecutionProfile] = None,
    ) -> ExecutionResult:
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
            "execution_profile": execution_profile.to_dict() if execution_profile else None,
            "timestamp": time.time(),
        }
        self.dispatches.append(record)

        # Check explicit overrides
        if task.task_id in self.task_results:
            res = self.task_results[task.task_id]
            res.is_reuse = reused
            return res

        # Check simulated rate limit / capacity errors
        if task.task_id in self.rate_limit_tasks:
            return ExecutionResult(
                success=False,
                error=f"HTTP 429: RESOURCE_EXHAUSTED for task '{task.task_id}'",
                duration=self.simulate_duration,
                is_reuse=reused,
                is_capacity_error=True,
            )

        if task.task_id in self.fail_tasks or not self.default_success:
            return ExecutionResult(
                success=False,
                error=f"Mock failure on task '{task.task_id}'",
                duration=self.simulate_duration,
                is_reuse=reused,
                is_capacity_error=False,
            )

        return ExecutionResult(
            success=True,
            result=dict(self.default_result),
            duration=self.simulate_duration,
            is_reuse=reused,
            is_capacity_error=False,
        )


class LocalExecutionAdapter(ExecutionAdapter):
    """
    Execution adapter that invokes a Python callable for each dispatched task.
    Enables local in-process testing or custom simulation handlers.
    """

    def __init__(
        self,
        handler: Callable[..., ExecutionResult]
    ) -> None:
        self._handler = handler
        self.dispatches: List[Dict[str, Any]] = []

    def dispatch(
        self,
        worker: Worker,
        task: Task,
        execution_profile: Optional[ExecutionProfile] = None,
    ) -> ExecutionResult:
        reused = self.is_reuse(worker)
        self.dispatches.append({
            "worker_id": worker.worker_id,
            "task_id": task.task_id,
            "is_reuse": reused,
            "execution_profile": execution_profile.to_dict() if execution_profile else None,
            "timestamp": time.time(),
        })

        # Support handler taking 2 args (worker, task) or 3 args (worker, task, execution_profile)
        sig = inspect.signature(self._handler)
        params_count = len(sig.parameters)
        if params_count >= 3:
            result = self._handler(worker, task, execution_profile)
        else:
            result = self._handler(worker, task)

        result.is_reuse = reused
        return result
