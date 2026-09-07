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
    WorkspaceAcquisitionError,
    WorkspaceConflictError,
)
from orchestrator.integration.manager import IntegrationManager
from orchestrator.integration.models import MergeResult
from orchestrator.models import Event, EventType, MissionState, Task, TaskState
from orchestrator.routing.models import ExecutionProfile
from orchestrator.routing.router import ModelRouter
from orchestrator.scheduler.aimd import AIMDConfig, AIMDController
from orchestrator.scheduler.feedback import FeedbackCollector, FeedbackSignal, FeedbackSignalType
from orchestrator.scheduler.ready_queue import ReadyQueue
from orchestrator.verification.engine import VerificationEngine
from orchestrator.workers.adapter import ExecutionAdapter, ExecutionResult, MockExecutionAdapter
from orchestrator.workers.affinity import DomainAffinityPolicy
from orchestrator.workers.models import Worker, WorkerState
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workspace.adapter import WorktreeAdapter
from orchestrator.workspace.models import WorkspaceMode, WorkspaceReleaseState
from orchestrator.workspace.registry import WorkspaceRegistry

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
    - WorkspaceRegistry: write-set exclusivity and worktree ownership
    - WorktreeAdapter: native/mock worktree provisioning
    - IntegrationManager: sequential automated merge queue
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
        workspace_registry: Optional[WorkspaceRegistry] = None,
        worktree_adapter: Optional[WorktreeAdapter] = None,
        integration_manager: Optional[IntegrationManager] = None,
        verification_engine: Optional[VerificationEngine] = None,
        on_task_started: Optional[Callable[[str], Task]] = None,
        on_task_completed: Optional[Callable[[str, Optional[Dict[str, Any]]], Tuple[Task, List[Task]]]] = None,
        on_task_failed: Optional[Callable[[str, str, bool], Task]] = None,
        event_emitter: Optional[Callable[[EventType, Optional[str], Optional[Dict[str, Any]]], Event]] = None,
        engine: Optional[MissionEngine] = None,
    ) -> None:
        self._ready_queue = ready_queue
        self._worker_registry = worker_registry
        self._affinity_policy = affinity_policy or DomainAffinityPolicy()
        self._execution_adapter = execution_adapter or MockExecutionAdapter()
        self._aimd_controller = aimd_controller or AIMDController()
        self._model_router = model_router or ModelRouter()
        self._feedback_collector = feedback_collector or FeedbackCollector()
        self._workspace_registry = workspace_registry or WorkspaceRegistry()
        self._worktree_adapter = worktree_adapter
        self._integration_manager = integration_manager
        self._verification_engine = verification_engine
        self._engine = engine

        self._on_task_started = on_task_started or (engine.mark_task_started if engine else None)
        self._on_task_completed = on_task_completed or (engine.mark_task_completed if engine else None)
        self._on_task_failed = on_task_failed or (engine.mark_task_failed if engine else None)
        self._event_emitter = event_emitter or (engine._emit if engine else None)

        if self._integration_manager is not None and self._event_emitter is not None:
            if getattr(self._integration_manager, "_queue", None) and self._integration_manager._queue._event_emitter is None:
                self._integration_manager._queue._event_emitter = self._event_emitter

        self._active_tasks: Dict[str, Task] = {}
        self._dispatches: List[ScheduledDispatch] = []
        self._is_evaluating: bool = False
        self._needs_reevaluation: bool = False


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
    def workspace_registry(self) -> WorkspaceRegistry:
        return self._workspace_registry

    @property
    def worktree_adapter(self) -> Optional[WorktreeAdapter]:
        return self._worktree_adapter

    @property
    def integration_manager(self) -> Optional[IntegrationManager]:
        return self._integration_manager

    @property
    def verification_engine(self) -> Optional[VerificationEngine]:
        return self._verification_engine

    @verification_engine.setter
    def verification_engine(self, engine: Optional[VerificationEngine]) -> None:
        self._verification_engine = engine

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

        # Link engine workspace and integration subsystems bidirectional
        if self._workspace_registry is not None:
            if hasattr(engine, "_workspace_registry"):
                engine._workspace_registry = self._workspace_registry
        elif hasattr(engine, "workspace_registry") and engine.workspace_registry is not None:
            self._workspace_registry = engine.workspace_registry

        if self._worktree_adapter is not None:
            if hasattr(engine, "_worktree_adapter"):
                engine._worktree_adapter = self._worktree_adapter
        elif hasattr(engine, "worktree_adapter") and engine.worktree_adapter is not None:
            self._worktree_adapter = engine.worktree_adapter

        if self._integration_manager is not None:
            if hasattr(engine, "_integration_manager"):
                engine._integration_manager = self._integration_manager
                engine._integration_manager.queue._on_merge_completed = engine._on_engine_merge_completed
                engine._integration_manager.queue._on_merge_failed = engine._on_engine_merge_failed
        elif hasattr(engine, "integration_manager") and engine.integration_manager is not None:
            self._integration_manager = engine.integration_manager

        if hasattr(engine, "verification_engine") and engine.verification_engine is not None:
            self._verification_engine = engine.verification_engine
        elif self._verification_engine is not None and hasattr(engine, "_verification_engine"):
            engine._verification_engine = self._verification_engine

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
            EventType.WORKSPACE_RELEASED,
            EventType.MERGE_COMPLETED,
            EventType.VERIFICATION_PASSED,
            EventType.REPAIR_COMPLETED,
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
        Single pass matching ready tasks against available idle workers subject to AIMD capacity
        and write-set collision freedom.
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

            # Controlled Workspace Acquisition Gate:
            # Check if task write set conflicts with any active workspace ownership
            can_acq, conflict_reason = self._workspace_registry.can_acquire(task)
            if not can_acq:
                if self._event_emitter:
                    self._event_emitter(
                        EventType.WORKSPACE_CONFLICT,
                        task_id=task.task_id,
                        payload={
                            "reason": conflict_reason,
                            "write_set": sorted(list(task.write_set)),
                        }
                    )
                # Task remains in ReadyQueue waiting for conflicting workspace to release.
                # Do NOT mark FAILED, do NOT decrease AIMD capacity!
                continue

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
        acquires workspace ownership, records feedback, emits events, and invokes execution adapter.
        """
        task_id = task.task_id
        worker_id = worker.worker_id
        is_reuse = self._execution_adapter.is_reuse(worker)
        self._active_tasks[task_id] = task

        # 1. Acquire workspace ownership
        workspace_record = self._workspace_registry.acquire(
            task=task,
            worker=worker,
            workspace_mode=task.workspace_mode,
            workspace_path=task.workspace_path,
            branch_name=task.branch_name,
        )
        task.workspace_path = workspace_record.workspace_path
        task.branch_name = workspace_record.branch_name
        task.assigned_worker_id = worker.worker_id

        # 2. Provision worktree if adapter is present and mode is branch
        if self._worktree_adapter is not None and task.workspace_mode == WorkspaceMode.BRANCH.value:
            try:
                actual_path = self._worktree_adapter.create_workspace(task, worker, task.branch_name)
                if actual_path:
                    task.workspace_path = actual_path
                    workspace_record.workspace_path = actual_path
            except Exception as e:
                self._workspace_registry.release(
                    task_id,
                    state=WorkspaceReleaseState.FAILED,
                    reason=str(e)
                )
                raise

        # 3. Emit WORKSPACE_ACQUIRED event
        if self._event_emitter:
            self._event_emitter(
                EventType.WORKSPACE_ACQUIRED,
                task_id=task_id,
                payload={
                    "worker_id": worker_id,
                    "workspace_mode": task.workspace_mode,
                    "workspace_path": task.workspace_path,
                    "branch_name": task.branch_name,
                    "write_set": sorted(list(task.write_set)),
                }
            )

        # 4. Route task to appropriate execution profile (FAST vs PRO)
        profile = self._model_router.route(task, worker, self._feedback_collector)

        # 5. Dequeue task from ReadyQueue
        self._ready_queue.remove(task_id)

        # 6. Assign worker in registry (transitions worker state to BUSY)
        self._worker_registry.assign_task(worker_id, task_id)

        # 7. Transition task to ASSIGNED then RUNNING
        if hasattr(task, "transition_to") and task.status == TaskState.READY:
            task.transition_to(TaskState.ASSIGNED)

        if self._on_task_started:
            self._on_task_started(task_id)
        else:
            task.transition_to(TaskState.RUNNING)

        # 8. Record feedback signal
        self._feedback_collector.record_task_started(task_id, worker_id)

        # 9. Emit structured events
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

        # 10. Dispatch to execution adapter with execution profile
        result = self._execution_adapter.dispatch(worker, task, profile)

        # 11. Handle synchronous result if returned
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
        Gated task completion.
        If verification_engine is configured:
          1. Transitions task to VERIFYING.
          2. Runs verification tiers (Tier 1 Self-Test, Tier 2 Independent).
          3. If passed:
             - Emits VERIFICATION_PASSED
             - Releases worker to IDLE
             - If merge required: submits to merge queue (MERGE_READY -> MERGED)
             - Else: releases workspace, marks task PASSED in engine, unlocks dependents
          4. If failed:
             - Checks repair eligibility (repairable failure & task.retry_count < task.max_retries)
             - If repairable: triggers in-context local repair with same worker and workspace,
               transitions VERIFYING -> RETRYING -> RUNNING -> REPAIR_COMPLETED -> VERIFYING.
             - If retries exhausted or unrepairable: transitions VERIFYING -> FAILED,
               releases workspace, releases worker, decrements AIMD capacity, blocks dependents.
        If verification_engine is None:
          Direct legacy completion without verification gating.
        """
        task = self._active_tasks.get(task_id) or (
            self._engine.graph.get_task(task_id)
            if self._engine and self._engine.graph.has_task(task_id)
            else None
        )
        worker = self._worker_registry.get_worker_for_task(task_id)
        worker_id = worker.worker_id if worker else (task.assigned_worker_id if task else "unknown")

        # ---------------------------------------------------------------------
        # Verification & Repair Pipeline (Phase 5)
        # ---------------------------------------------------------------------
        if self._verification_engine is not None and task is not None:
            # 1. Transition task to VERIFYING
            if task.status == TaskState.RUNNING:
                task.transition_to(TaskState.VERIFYING)
                if self._event_emitter:
                    self._event_emitter(EventType.TASK_VERIFYING, task_id=task_id)

            # 2. Execute verification tiers
            verif_result = self._verification_engine.verify_task(task)

            if not verif_result.passed:
                # Verification failed! Evaluate local repair eligibility
                can_repair = (
                    worker is not None
                    and self._verification_engine.repair_coordinator.can_repair(task, verif_result)
                )

                if can_repair:
                    # In-Context Local Repair Loop (reusing same worker & workspace)
                    repair_payload = self._verification_engine.repair_coordinator.build_repair_payload(
                        task, verif_result
                    )
                    if self._event_emitter:
                        self._event_emitter(
                            EventType.REPAIR_REQUESTED,
                            task_id=task_id,
                            payload=repair_payload.to_dict()
                        )

                    # Transition VERIFYING -> RETRYING (increments retry_count)
                    task.transition_to(TaskState.RETRYING, reason=verif_result.summary)
                    if self._event_emitter:
                        self._event_emitter(
                            EventType.TASK_RETRYING,
                            task_id=task_id,
                            payload={"retry_count": task.retry_count, "error": verif_result.summary}
                        )

                    # Transition RETRYING -> RUNNING for repair execution
                    task.transition_to(TaskState.RUNNING)

                    # Request repair from worker via execution adapter
                    repair_res = self._execution_adapter.request_repair(worker, task, repair_payload)

                    if repair_res is not None:
                        if repair_res.success:
                            if self._event_emitter:
                                self._event_emitter(
                                    EventType.REPAIR_COMPLETED,
                                    task_id=task_id,
                                    payload={
                                        "retry_count": task.retry_count,
                                        "worker_id": worker.worker_id,
                                    }
                                )
                            # Re-enter verification loop
                            self.complete_task(
                                task_id=task_id,
                                result=repair_res.result or result,
                                duration=repair_res.duration or duration,
                            )
                            return
                        else:
                            # Worker repair failed
                            if self._event_emitter:
                                self._event_emitter(
                                    EventType.REPAIR_FAILED,
                                    task_id=task_id,
                                    payload={"error": repair_res.error or "Repair execution failed"}
                                )
                            self.fail_task(
                                task_id=task_id,
                                error=repair_res.error or "Repair execution failed",
                                can_retry=False,
                                duration=duration,
                            )
                            return
                    else:
                        # Async repair underway
                        return
                else:
                    # Non-repairable or retries exhausted
                    self.fail_task(
                        task_id=task_id,
                        error=verif_result.summary,
                        can_retry=False,
                        duration=duration,
                    )
                    return

        # ---------------------------------------------------------------------
        # Acceptance & Integration (Verified or Legacy Mode)
        # ---------------------------------------------------------------------
        self._active_tasks.pop(task_id, None)

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

        # 2. Release worker to IDLE (preserving warm context for reuse)
        if worker:
            self._worker_registry.release_worker(
                worker_id=worker.worker_id,
                success=True,
                duration=duration,
            )
            if self._workspace_registry is not None:
                self._workspace_registry.release_worker_lock(worker.worker_id)
            if self._event_emitter:
                self._event_emitter(
                    EventType.WORKER_IDLE,
                    task_id=task_id,
                    payload={"worker_id": worker.worker_id, "domain": worker.domain}
                )

        # 3. Controlled Integration / Merge Queue handling
        requires_merge = (
            self._integration_manager is not None
            and task is not None
            and task.workspace_mode == WorkspaceMode.BRANCH.value
            and len(task.write_set) > 0
        )

        if requires_merge and task is not None:
            if task.can_transition_to(TaskState.PASSED):
                task.transition_to(TaskState.PASSED)
            task.result = result or {}

            # Finalize workspace changes in worktree adapter if present
            if self._worktree_adapter is not None:
                try:
                    self._worktree_adapter.finalize_workspace(task.workspace_path)
                except Exception:
                    pass

            # Enqueue into sequential merge queue (emits MERGE_READY)
            self._integration_manager.submit_for_merge(
                task=task,
                worker_id=worker_id,
                branch_name=task.branch_name,
                workspace_path=task.workspace_path,
            )

            # Sequentially process pending merges
            self._integration_manager.process_pending_merges()

        else:
            # Direct release if no merge required
            self._workspace_registry.release(
                task_id=task_id,
                state=WorkspaceReleaseState.RELEASED,
                reason="Task completed and verified without merge queue"
            )
            if self._event_emitter:
                self._event_emitter(
                    EventType.WORKSPACE_RELEASED,
                    task_id=task_id,
                    payload={"state": WorkspaceReleaseState.RELEASED.value}
                )

            # Notify engine callback
            if self._on_task_completed:
                self._on_task_completed(task_id, result)
            elif task is not None:
                if task.can_transition_to(TaskState.PASSED):
                    task.transition_to(TaskState.PASSED)
                task.result = result or {}

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
        Releases worker and workspace ownership, marks task failed, updates feedback and AIMD.
        Preserves retry eligibility and ensures no stale workspace locks survive.
        """
        task = self._active_tasks.pop(task_id, None) or (
            self._engine.graph.get_task(task_id)
            if self._engine and self._engine.graph.has_task(task_id)
            else None
        )
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

        # 2. Release workspace ownership cleanly
        if self._workspace_registry:
            self._workspace_registry.release(
                task_id=task_id,
                state=WorkspaceReleaseState.FAILED,
                reason=error
            )
            if self._event_emitter:
                self._event_emitter(
                    EventType.WORKSPACE_RELEASED,
                    task_id=task_id,
                    payload={"state": WorkspaceReleaseState.FAILED.value, "reason": error}
                )

        if worker_failed and self._workspace_registry and worker:
            self._workspace_registry.release_for_worker(
                worker_id=worker.worker_id,
                state=WorkspaceReleaseState.FAILED,
                reason=f"Worker failure: {error}"
            )

        # 3. Release worker
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

        # 4. Notify engine callback or transition directly
        if self._on_task_failed:
            self._on_task_failed(task_id, error, can_retry)
        elif task is not None:
            if can_retry and task.retry_count < task.max_retries and task.can_transition_to(TaskState.RETRYING):
                task.transition_to(TaskState.RETRYING, reason=error)
            elif task.can_transition_to(TaskState.FAILED):
                task.transition_to(TaskState.FAILED, reason=error)

        # 5. If task is eligible for retry, re-enqueue it via engine.retry_task
        if can_retry and self._engine is not None:
            task = self._engine.graph.get_task(task_id) if self._engine.graph.has_task(task_id) else None
            if task and task.status == TaskState.RETRYING:
                self._engine.retry_task(task_id)

        # 6. Continuous execution: re-evaluate remaining independent ready tasks!
        self.evaluate()


    def handle_worker_failure(self, worker_id: str, error: str = "") -> None:
        """
        Handles an unexpected worker loss during execution.
        Safely releases its workspace ownership, marks the worker FAILED,
        and transitions its active task into RETRYING for reassignment.
        """
        worker = self._worker_registry.get_worker(worker_id)
        if not worker or not worker.current_task_id:
            if worker:
                self._worker_registry.release_worker(worker_id, success=False, error=error, worker_failed=True)
                if self._event_emitter:
                    self._event_emitter(EventType.WORKER_FAILED, payload={"worker_id": worker_id, "error": error})
            return

        task_id = worker.current_task_id
        self.fail_task(
            task_id=task_id,
            error=error or f"Worker '{worker_id}' terminated unexpectedly",
            can_retry=True,
            worker_failed=True,
        )
