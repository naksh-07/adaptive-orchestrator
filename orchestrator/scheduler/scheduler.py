"""
Adaptive Orchestrator v5 - Event-Driven Scheduler.
Consumes ReadyQueue, matches available workers in WorkerRegistry, gates dispatch via
AIMD Adaptive Concurrency Controller, routes models via ModelRouter, and dispatches execution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple

from orchestrator.exceptions import (
    AdaptiveOrchestratorError,
    NoAvailableWorkerError,
    TaskNotReadyError,
    WorkerNotFoundError,
)
from orchestrator.models import Event, EventType, MissionState, Task, TaskState
from orchestrator.routing.models import ExecutionProfile
from orchestrator.routing.router import ModelRouter
from orchestrator.scheduler.aimd import AIMDConfig, AIMDController
from orchestrator.scheduler.feedback import FeedbackCollector, FeedbackSignal, FeedbackSignalType
from orchestrator.scheduler.ready_queue import ReadyQueue
from orchestrator.workers.adapter import ExecutionAdapter, ExecutionResult, MockExecutionAdapter
from orchestrator.workers.affinity import DomainAffinityPolicy
from orchestrator.workers.models import Worker, WorkerState
from orchestrator.workers.registry import WorkerRegistry

if TYPE_CHECKING:
    from orchestrator.engine import MissionEngine


@dataclass
class ScheduledDispatch:
    """
    Record of a task dispatched to a worker by the scheduler.
    """
    task_id: str
    worker_id: str
    domain: str
    is_reuse: bool
    execution_profile: Optional[ExecutionProfile] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "domain": self.domain,
            "is_reuse": self.is_reuse,
            "execution_profile": self.execution_profile.to_dict() if self.execution_profile else None,
            "timestamp": self.timestamp,
        }


class EventDrivenScheduler:
    """
    Event-driven continuous scheduler.
    Answers: WHAT SHOULD RUN NOW, ON WHICH AVAILABLE WORKER, UNDER WHAT CAPACITY AND MODEL TIER?
    
    Coordinates:
    - ReadyQueue: logical task ordering
    - WorkerRegistry: worker tracking and in-memory states
    - DomainAffinityPolicy: domain compatibility
    - AIMDController: dynamic physical active concurrency capacity
    - ModelRouter: model tier selection (FAST vs PRO)
    - ExecutionAdapter: physical invocation boundary
    """

    def __init__(
        self,
        ready_queue: ReadyQueue,
        worker_registry: WorkerRegistry,
        affinity_policy: Optional[DomainAffinityPolicy] = None,
        execution_adapter: Optional[ExecutionAdapter] = None,
        aimd_controller: Optional[AIMDController] = None,
        model_router: Optional[ModelRouter] = None,
        feedback_collector: Optional[FeedbackCollector] = None,
        on_task_started: Optional[Callable[[str], Task]] = None,
        on_task_completed: Optional[Callable[[str, Optional[Dict[str, Any]]], Tuple[Task, List[Task]]]] = None,
        on_task_failed: Optional[Callable[[str, str, bool], Task]] = None,
        event_emitter: Optional[Callable[[EventType, Optional[str], Optional[Dict[str, Any]]], Event]] = None,
    ) -> None:
        self._ready_queue = ready_queue
        self._worker_registry = worker_registry
        self._affinity_policy = affinity_policy or DomainAffinityPolicy()
        self._execution_adapter = execution_adapter or MockExecutionAdapter()
        self._aimd_controller = aimd_controller or AIMDController()
        self._model_router = model_router or ModelRouter()
        self._feedback_collector = feedback_collector or FeedbackCollector()

        self._on_task_started = on_task_started
        self._on_task_completed = on_task_completed
        self._on_task_failed = on_task_failed
        self._event_emitter = event_emitter

        self._dispatches: List[ScheduledDispatch] = []
        self._is_evaluating: bool = False
        self._needs_reevaluation: bool = False
        self._engine: Optional[MissionEngine] = None

    @property
    def ready_queue(self) -> ReadyQueue:
        return self._ready_queue

    @property
    def worker_registry(self) -> WorkerRegistry:
        return self._worker_registry

    @property
    def affinity_policy(self) -> DomainAffinityPolicy:
        return self._affinity_policy

    @property
    def execution_adapter(self) -> ExecutionAdapter:
        return self._execution_adapter

    @property
    def aimd_controller(self) -> AIMDController:
        return self._aimd_controller

    @property
    def model_router(self) -> ModelRouter:
        return self._model_router

    @property
    def feedback_collector(self) -> FeedbackCollector:
        return self._feedback_collector

    @property
    def dispatches(self) -> List[ScheduledDispatch]:
        """Chronological record of all scheduled dispatches."""
        return list(self._dispatches)

    def attach_to_engine(self, engine: MissionEngine) -> None:
        """
        Attaches scheduler to a MissionEngine facade, subscribing to its event stream
        and wiring its task state mutation callbacks.
        """
        self._engine = engine
        self._on_task_started = engine.mark_task_started
        self._on_task_completed = engine.mark_task_completed
        self._on_task_failed = engine.mark_task_failed
        self._event_emitter = engine.emit_event

        # Subscribe to reactive events
        engine.subscribe(self._handle_engine_event)

    def _handle_engine_event(self, event: Event) -> None:
        """
        Reacts to incoming engine events to trigger scheduler evaluation.
        """
        triggering_events = (
            EventType.TASK_READY,
            EventType.WORKER_IDLE,
            EventType.MISSION_STATE_CHANGED,
            EventType.DEPENDENCY_SATISFIED,
            EventType.CAPACITY_CHANGED,
        )
        if event.event_type in triggering_events:
            self.evaluate()

    def evaluate(self) -> List[ScheduledDispatch]:
        """
        Evaluates the current state of ReadyQueue, available workers, and AIMD capacity.
        Continuously dispatches compatible pairs without wave barriers.
        Non-recursive loop with re-entrancy guard.
        """
        if self._is_evaluating:
            self._needs_reevaluation = True
            return []

        self._is_evaluating = True
        total_dispatches: List[ScheduledDispatch] = []

        try:
            while True:
                self._needs_reevaluation = False
                step_dispatches = self._evaluate_step()
                total_dispatches.extend(step_dispatches)

                # If no work was dispatched and no re-evaluation was requested, we are steady
                if not step_dispatches and not self._needs_reevaluation:
                    break
        finally:
            self._is_evaluating = False

        return total_dispatches

    def _evaluate_step(self) -> List[ScheduledDispatch]:
        """
        Single pass matching ready tasks against available idle workers subject to AIMD capacity.
        """
        if self._engine is not None and self._engine.mission.state != MissionState.EXECUTING:
            return []

        if self._ready_queue.is_empty():
            return []

        idle_workers = self._worker_registry.get_idle_workers()
        if not idle_workers:
            return []

        # Check physical active concurrency against AIMD capacity
        current_active = self._worker_registry.busy_count
        if not self._aimd_controller.can_dispatch(current_active):
            # Backpressure: physical capacity is fully saturated!
            return []

        ready_tasks = self._ready_queue.all_tasks()
        step_dispatches: List[ScheduledDispatch] = []
        assigned_worker_ids: Set[str] = set()

        for task in ready_tasks:
            # Re-check capacity gate accounting for newly assigned tasks in this step
            total_active = current_active + len(step_dispatches)
            if not self._aimd_controller.can_dispatch(total_active):
                break  # Capacity ceiling reached for this step

            # Filter idle workers not already assigned in this step
            available_workers = [
                w for w in idle_workers
                if w.worker_id not in assigned_worker_ids and w.is_available
            ]
            if not available_workers:
                break

            # Select worker via domain affinity policy
            selected_worker = self._affinity_policy.select_worker(task, available_workers)
            if selected_worker is None:
                continue

            # Lock in assignment
            assigned_worker_ids.add(selected_worker.worker_id)
            dispatch = self._dispatch_task(selected_worker, task)
            step_dispatches.append(dispatch)

        return step_dispatches

    def _dispatch_task(self, worker: Worker, task: Task) -> ScheduledDispatch:
        """
        Executes assignment of task to worker, routes model tier, updates states,
        records feedback, emits events, and invokes execution adapter.
        """
        task_id = task.task_id
        worker_id = worker.worker_id
        is_reuse = self._execution_adapter.is_reuse(worker)

        # 1. Route task to appropriate execution profile (FAST vs PRO)
        profile = self._model_router.route(task, worker, self._feedback_collector)

        # 2. Dequeue task from ReadyQueue
        self._ready_queue.remove(task_id)

        # 3. Assign worker in registry (transitions worker state to BUSY)
        self._worker_registry.assign_task(worker_id, task_id)

        # 4. Transition task to RUNNING via engine callback or direct mutation
        if self._on_task_started:
            self._on_task_started(task_id)
        else:
            task.transition_to(TaskState.RUNNING)

        # 5. Record feedback signal
        self._feedback_collector.record_task_started(task_id, worker_id)

        # 6. Emit structured events
        if self._event_emitter:
            self._event_emitter(
                EventType.TASK_ASSIGNED,
                task_id=task_id,
                payload={
                    "worker_id": worker_id,
                    "domain": worker.domain,
                    "is_reuse": is_reuse,
                    "model_tier": profile.tier.value,
                    "model_id": profile.model_id,
                    "routing_reason": profile.routing_reason,
                }
            )
            self._event_emitter(
                EventType.WORKER_BUSY,
                task_id=task_id,
                payload={
                    "worker_id": worker_id,
                    "domain": worker.domain,
                    "model_tier": profile.tier.value,
                }
            )

        dispatch = ScheduledDispatch(
            task_id=task_id,
            worker_id=worker_id,
            domain=worker.domain,
            is_reuse=is_reuse,
            execution_profile=profile,
        )
        self._dispatches.append(dispatch)

        # 7. Dispatch to execution adapter with execution profile
        result = self._execution_adapter.dispatch(worker, task, profile)

        # 8. Handle synchronous result if returned
        if result is not None:
            if result.success:
                self.complete_task(
                    task_id=task_id,
                    result=result.result,
                    duration=result.duration
                )
            else:
                self.fail_task(
                    task_id=task_id,
                    error=result.error or "Execution failed",
                    can_retry=False,
                    duration=result.duration,
                    is_capacity_error=result.is_capacity_error,
                )

        return dispatch

    def complete_task(
        self,
        task_id: str,
        result: Optional[Dict[str, Any]] = None,
        duration: float = 0.0
    ) -> None:
        """
        Releases worker to IDLE, marks task completed, updates feedback and AIMD capacity.
        Unlocks downstream tasks and immediately re-evaluates ready queue.
        """
        worker = self._worker_registry.get_worker_for_task(task_id)
        worker_id = worker.worker_id if worker else "unknown"

        # 1. Record feedback and process AIMD controller
        self._feedback_collector.record_task_completed(
            task_id=task_id,
            worker_id=worker_id,
            latency=duration,
        )

        old_capacity = self._aimd_controller.current_capacity
        new_capacity = self._aimd_controller.process_feedback(
            FeedbackSignal(
                signal_type=FeedbackSignalType.TASK_COMPLETED,
                task_id=task_id,
                worker_id=worker_id,
                latency=duration,
            )
        )

        if new_capacity != old_capacity and self._event_emitter:
            self._event_emitter(
                EventType.CAPACITY_CHANGED,
                task_id=task_id,
                payload={
                    "old_capacity": old_capacity,
                    "new_capacity": new_capacity,
                    "action": "increase",
                }
            )

        # 2. Release worker to IDLE (preserving warm context)
        if worker:
            self._worker_registry.release_worker(
                worker_id=worker.worker_id,
                success=True,
                duration=duration,
            )
            if self._event_emitter:
                self._event_emitter(
                    EventType.WORKER_IDLE,
                    task_id=task_id,
                    payload={"worker_id": worker.worker_id, "domain": worker.domain}
                )

        # 3. Notify engine callback
        if self._on_task_completed:
            self._on_task_completed(task_id, result)

        # 4. Continuous execution: immediately re-evaluate queue!
        self.evaluate()

    def fail_task(
        self,
        task_id: str,
        error: str = "",
        can_retry: bool = False,
        duration: float = 0.0,
        worker_failed: bool = False,
        is_capacity_error: bool = False,
    ) -> None:
        """
        Releases worker, marks task failed, updates feedback and AIMD controller.
        Detects rate-limit/capacity congestion signals to trigger multiplicative decrease.
        Triggers follow-up evaluation for remaining independent ready tasks.
        """
        worker = self._worker_registry.get_worker_for_task(task_id)
        worker_id = worker.worker_id if worker else "unknown"

        # Check if error string indicates rate limit / capacity exhaustion
        err_lower = (error or "").lower()
        is_cap = is_capacity_error or ("429" in error or "resource_exhausted" in err_lower or "rate limit" in err_lower)

        # 1. Record feedback and process AIMD controller
        self._feedback_collector.record_task_failed(
            task_id=task_id,
            worker_id=worker_id,
            error=error,
            is_capacity_error=is_cap,
            latency=duration,
            worker_failed=worker_failed,
        )

        old_capacity = self._aimd_controller.current_capacity
        sig_type = (
            FeedbackSignalType.RATE_LIMIT_ERROR
            if is_cap
            else (FeedbackSignalType.WORKER_FAILED if worker_failed else FeedbackSignalType.TASK_FAILED)
        )
        new_capacity = self._aimd_controller.process_feedback(
            FeedbackSignal(
                signal_type=sig_type,
                task_id=task_id,
                worker_id=worker_id,
                error=error,
                is_capacity_error=is_cap,
                latency=duration,
            )
        )

        if new_capacity != old_capacity and self._event_emitter:
            self._event_emitter(
                EventType.CAPACITY_CHANGED,
                task_id=task_id,
                payload={
                    "old_capacity": old_capacity,
                    "new_capacity": new_capacity,
                    "action": "decrease",
                    "reason": error,
                }
            )

        # 2. Release worker
        if worker:
            self._worker_registry.release_worker(
                worker_id=worker.worker_id,
                success=False,
                duration=duration,
                error=error,
                worker_failed=worker_failed,
            )
            if self._event_emitter:
                ev_type = EventType.WORKER_FAILED if worker_failed else EventType.WORKER_IDLE
                self._event_emitter(
                    ev_type,
                    task_id=task_id,
                    payload={"worker_id": worker.worker_id, "error": error}
                )

        # 3. Notify engine callback
        if self._on_task_failed:
            self._on_task_failed(task_id, error, can_retry)

        # 4. Continuous execution: re-evaluate remaining independent ready tasks!
        self.evaluate()
