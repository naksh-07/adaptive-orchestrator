"""
Adaptive Orchestrator v5 - Core Engine Facade.
Connects Mission -> DependencyGraph -> DependencyResolver -> ReadyQueue -> EventEmitter.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

from orchestrator.exceptions import (
    AdaptiveOrchestratorError,
    GraphMutationError,
    InvalidStateTransitionError,
    TaskNotFoundError,
    TaskNotReadyError,
)
from orchestrator.graph.dag import DependencyGraph
from orchestrator.graph.mutations import GraphMutationEngine
from orchestrator.integration.manager import IntegrationManager
from orchestrator.models import (
    Event,
    EventType,
    Mission,
    MissionState,
    Task,
    TaskState,
)
from orchestrator.verification.models import VictoryAuditResult
from orchestrator.verification.verifier import Tier4VictoryAuditVerifier
from orchestrator.resolver import DependencyResolver
from orchestrator.scheduler.ready_queue import ReadyQueue
from orchestrator.scheduler.scheduler import EventDrivenScheduler, ScheduledDispatch
from orchestrator.verification.engine import VerificationEngine
from orchestrator.workers.models import Worker
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workspace.adapter import WorktreeAdapter
from orchestrator.workspace.models import WorkspaceMode, WorkspaceReleaseState
from orchestrator.workspace.registry import WorkspaceRegistry
 
 
class TaskList(list):
    """
    List of tasks supporting both Task instances and string task_id checks in __contains__.
    """
    def __contains__(self, item: Any) -> bool:
        if isinstance(item, str):
            return any(getattr(t, "task_id", None) == item or getattr(t, "id", None) == item for t in self)
        return super().__contains__(item)


class MissionEngine:
    """
    Foundational Core Engine for Adaptive Orchestrator v5.
    Encapsulates:
      - Mission lifecycle and state
      - Topological DependencyGraph
      - Dynamic GraphMutationEngine
      - DependencyResolver for logical readiness
      - Priority ReadyQueue
      - Reusable WorkerRegistry / Pool
      - WorkspaceRegistry & WorktreeAdapter
      - IntegrationManager & MergeQueue
      - EventDrivenScheduler
      - Deterministic Event Emitter & Audit Log
    """

    def __init__(
        self,
        mission_id: str,
        title: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        worker_registry: Optional[WorkerRegistry] = None,
        scheduler: Optional[EventDrivenScheduler] = None,
        workspace_registry: Optional[WorkspaceRegistry] = None,
        worktree_adapter: Optional[WorktreeAdapter] = None,
        integration_manager: Optional[IntegrationManager] = None,
        verification_engine: Optional[VerificationEngine] = None,
        persistence_manager: Optional[Any] = None,
        telemetry_collector: Optional[Any] = None,
        auto_audit: bool = True,
        tier4_verifier: Optional[Tier4VictoryAuditVerifier] = None,
        required_artifacts: Optional[List[str]] = None,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._mission = Mission(
            mission_id=mission_id,
            title=title,
            description=description,
            state=MissionState.DRAFTING,
            metadata=metadata or {},
        )
        self._graph = DependencyGraph()
        self._resolver = DependencyResolver(self._graph)
        self._mutations = GraphMutationEngine(self._graph)
        self._ready_queue = ReadyQueue()
        self._workers = worker_registry or WorkerRegistry()
        self._workspace_registry = workspace_registry or WorkspaceRegistry()
        self._worktree_adapter = worktree_adapter
        self._integration_manager = integration_manager
        self._verification_engine = verification_engine
        self._scheduler = scheduler
        self._persistence_manager = persistence_manager
        self._telemetry_collector = telemetry_collector
        self._auto_audit = auto_audit
        self._tier4_verifier = tier4_verifier
        self._required_artifacts = required_artifacts
        self._acceptance_criteria = acceptance_criteria

        if self._integration_manager is not None:
            self._integration_manager.queue._on_merge_completed = self._on_engine_merge_completed
            self._integration_manager.queue._on_merge_failed = self._on_engine_merge_failed

        if self._verification_engine is not None and self._scheduler is not None:
            self._scheduler.verification_engine = self._verification_engine

        self._events: List[Event] = []
        self._event_listeners: List[Callable[[Event], None]] = []
        self._sequence_counter: int = 0

        # Emit initial MISSION_CREATED event
        self._emit(
            EventType.MISSION_CREATED,
            payload={"title": title, "state": self._mission.state.value}
        )

        if self._scheduler is not None:
            self._scheduler.attach_to_engine(self)

        if self._persistence_manager is not None:
            self._persistence_manager.attach_to_engine(self)

        if self._telemetry_collector is not None:
            self._telemetry_collector.attach_to_engine(self)

    @property
    def persistence_manager(self) -> Optional[Any]:
        """Returns attached persistence manager, if any."""
        return self._persistence_manager

    @property
    def telemetry_collector(self) -> Optional[Any]:
        """Returns attached telemetry collector, if any."""
        return self._telemetry_collector

    @property
    def auto_audit(self) -> bool:
        """Returns whether Tier 4 Victory Audit is automatically triggered upon task completion."""
        return self._auto_audit

    @auto_audit.setter
    def auto_audit(self, value: bool) -> None:
        self._auto_audit = value

    def attach_persistence_manager(self, persistence_manager: Any) -> None:
        """Attaches a PersistenceManager to the engine."""
        self._persistence_manager = persistence_manager
        persistence_manager.attach_to_engine(self)

    def attach_persistence(self, persistence_manager: Any) -> None:
        """Alias for attach_persistence_manager."""
        self.attach_persistence_manager(persistence_manager)

    def attach_telemetry_collector(self, telemetry_collector: Any) -> None:
        """Attaches a TelemetryCollector to the engine."""
        self._telemetry_collector = telemetry_collector
        telemetry_collector.attach_to_engine(self)

    def attach_telemetry(self, telemetry_collector: Any) -> None:
        """Alias for attach_telemetry_collector."""
        self.attach_telemetry_collector(telemetry_collector)

    @property
    def mission(self) -> Mission:
        """Returns the mission entity."""
        return self._mission

    @property
    def state(self) -> MissionState:
        """Returns current mission state."""
        return self._mission.state

    @property
    def tasks(self) -> Dict[str, Task]:
        """Returns dictionary of all tasks by task_id."""
        return {t.task_id: t for t in self._graph.all_tasks()}

    @property
    def graph(self) -> DependencyGraph:
        """Returns the underlying dependency graph."""
        return self._graph

    @property
    def ready_queue(self) -> ReadyQueue:
        """Returns the ready queue."""
        return self._ready_queue

    @property
    def ready_queue_size(self) -> int:
        """Returns the number of ready tasks currently enqueued."""
        return len(self._ready_queue)

    @property
    def workers(self) -> WorkerRegistry:
        """Returns the worker registry."""
        return self._workers

    @property
    def worker_registry(self) -> WorkerRegistry:
        """Returns the worker registry (canonical alias)."""
        return self._workers

    @property
    def scheduler(self) -> Optional[EventDrivenScheduler]:
        """Returns the attached scheduler, if any."""
        return self._scheduler

    @property
    def workspace_registry(self) -> WorkspaceRegistry:
        """Returns the workspace ownership registry."""
        return self._workspace_registry

    @property
    def worktree_adapter(self) -> Optional[WorktreeAdapter]:
        """Returns the worktree adapter, if configured."""
        return self._worktree_adapter

    @property
    def integration_manager(self) -> Optional[IntegrationManager]:
        """Returns the integration manager, if configured."""
        return self._integration_manager

    @property
    def aimd_controller(self) -> Any:
        """Returns attached scheduler's AIMD controller, if any, or default AIMDController."""
        if self._scheduler and self._scheduler.aimd_controller:
            return self._scheduler.aimd_controller
        if not hasattr(self, "_default_aimd") or self._default_aimd is None:
            from orchestrator.scheduler.aimd import AIMDController
            self._default_aimd = AIMDController()
        return self._default_aimd

    def get_task(self, task_id: str) -> Task:
        """Returns the task with the specified task_id from the dependency graph."""
        return self._graph.get_task(task_id)

    def get_tasks_by_state(self, state: Union[TaskState, str]) -> List[Task]:
        """Returns all tasks in the mission graph with the given state."""
        st_val = state.value if hasattr(state, "value") else str(state)
        return [t for t in self._graph.all_tasks() if t.status.value == st_val]

    @property
    def model_router(self) -> Optional[Any]:
        """Returns attached scheduler's model router, if any."""
        return self._scheduler.model_router if self._scheduler else None

    @property
    def feedback_collector(self) -> Optional[Any]:
        """Returns attached scheduler's feedback collector, if any."""
        return self._scheduler.feedback_collector if self._scheduler else None

    @property
    def verification_engine(self) -> Optional[VerificationEngine]:
        """Returns attached verification engine, if configured."""
        return self._verification_engine

    def attach_verification_engine(self, verification_engine: VerificationEngine) -> None:
        """
        Attaches a VerificationEngine and links it to the attached scheduler.
        """
        self._verification_engine = verification_engine
        if self._scheduler is not None:
            self._scheduler.verification_engine = verification_engine

    def attach_integration_manager(self, integration_manager: IntegrationManager) -> None:
        """
        Attaches an IntegrationManager and wires merge callbacks.
        """
        self._integration_manager = integration_manager
        self._integration_manager.queue._on_merge_completed = self._on_engine_merge_completed
        self._integration_manager.queue._on_merge_failed = self._on_engine_merge_failed
        if self._scheduler is not None:
            self._scheduler._integration_manager = integration_manager

    def _on_engine_merge_completed(self, task_id: str, res: Any) -> None:
        commit_id = getattr(res, "commit_id", None)
        self.mark_task_merged(task_id, commit_id=commit_id)

    def _on_engine_merge_failed(self, task_id: str, res: Any) -> None:
        err = getattr(res, "error", "Merge conflict")
        self.mark_task_failed(task_id, error=err, can_retry=False)

    def attach_scheduler(
        self,
        scheduler: EventDrivenScheduler,
        worker_registry: Optional[WorkerRegistry] = None,
    ) -> None:
        """
        Attaches an EventDrivenScheduler and optional WorkerRegistry.
        Wires event subscriptions and callbacks.
        """
        if worker_registry is not None:
            self._workers = worker_registry
        self._scheduler = scheduler
        if self._verification_engine is not None:
            scheduler.verification_engine = self._verification_engine
        elif scheduler.verification_engine is not None:
            self._verification_engine = scheduler.verification_engine
        self._scheduler.attach_to_engine(self)

    def register_worker(
        self,
        worker_or_id: Optional[Union[Worker, str]] = None,
        domains: Optional[List[str]] = None,
        max_concurrency: int = 1,
        worker_id: Optional[str] = None,
    ) -> Worker:
        """
        Registers a worker into the engine's worker registry and emits WORKER_REGISTERED.
        Accepts either a Worker object or worker_id and domains list.
        """
        wid = worker_id or (worker_or_id if isinstance(worker_or_id, str) else None)
        if isinstance(worker_or_id, Worker):
            worker = worker_or_id
        else:
            if not wid:
                raise ValueError("Must provide a worker_id or Worker instance")
            domain_list = domains or ["general"]
            worker = Worker(
                worker_id=wid,
                domain=domain_list[0],
                supported_domains=set(domain_list),
                max_concurrency=max_concurrency,
            )
        self._workers.register_worker(worker)
        self._emit(
            EventType.WORKER_REGISTERED,
            payload={"worker_id": worker.worker_id, "domain": worker.domain}
        )
        return worker

    def assign_next(self) -> Optional[ScheduledDispatch]:
        """
        Dispatches the next eligible task from the ready queue to an available compatible worker.
        Uses attached scheduler if present, or performs direct matching via worker registry.
        """
        if self._scheduler is not None:
            dispatches = self._scheduler.evaluate()
            return dispatches[0] if dispatches else None

        task = self._ready_queue.pop_optional()
        if not task:
            return None

        # Find compatible idle worker
        worker = None
        for w in self._workers.get_idle_workers():
            if task.domain in w.supported_domains or "general" in w.supported_domains or w.domain == task.domain:
                worker = w
                break

        if not worker:
            self._ready_queue.push(task)
            return None

        self.mark_task_assigned(task.task_id, worker_id=worker.worker_id)
        self.mark_task_started(task.task_id)
        self._workers.assign_task(worker.worker_id, task.task_id)
        dispatch = ScheduledDispatch(
            task_id=task.task_id,
            worker_id=worker.worker_id,
            domain=task.domain,
            is_reuse=(worker.tasks_completed > 0),
            execution_profile=getattr(task, "execution_profile", None),
        )
        return dispatch

    def handle_worker_failure(self, worker_id: str, reason: str = "") -> None:
        """
        Handles worker failure: transitions worker to DEGRADED,
        finds task assigned to worker, resets task to READY (or FAILED if retries exhausted),
        and releases any workspace locks held by the task.
        """
        if self._scheduler is not None:
            self._scheduler.handle_worker_failure(worker_id=worker_id, error=reason)
            return

        worker = self._workers.get(worker_id)
        if not worker:
            return

        worker.mark_failed(reason)
        # Find any task assigned to this worker
        for task in self._graph.all_tasks():
            if task.assigned_worker_id == worker_id and task.status.is_active:
                task.assigned_worker_id = None
                if task.retry_count < task.max_retries:
                    task.transition_to(TaskState.RETRYING, reason=f"Worker failure: {reason}")
                    task.transition_to(TaskState.READY, reason=f"Worker failure: {reason}")
                    if not self._ready_queue.contains(task.task_id):
                        unlock_val = self._resolver.calculate_unlock_value(task.task_id)
                        self._ready_queue.push(task, unlock_value=unlock_val)
                else:
                    task.transition_to(TaskState.FAILED, reason=f"Worker failure: {reason} (retries exhausted)")

                # Release workspace if held
                if self._workspace_registry.is_locked(task.task_id):
                    self._workspace_registry.release(task.task_id)

        self._emit(
            EventType.WORKER_FAILED,
            payload={"worker_id": worker_id, "reason": reason}
        )

    def evaluate_scheduler(self) -> List[ScheduledDispatch]:
        """
        Manually triggers an evaluation step on the attached scheduler, returning any dispatches.
        """
        if self._scheduler is not None:
            return self._scheduler.evaluate()
        return []

    # -------------------------------------------------------------------------
    # Event System
    # -------------------------------------------------------------------------

    def subscribe(self, listener: Callable[[Event], None]) -> None:
        """Subscribes a callable listener to receive emitted events in real time."""
        self._event_listeners.append(listener)

    def get_events(self) -> List[Event]:
        """Returns a copy of all emitted events in chronological sequence."""
        return list(self._events)

    def _emit(
        self,
        event_type: EventType,
        task_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None
    ) -> Event:
        self._sequence_counter += 1
        event = Event(
            event_type=event_type,
            mission_id=self._mission.mission_id,
            task_id=task_id,
            timestamp=time.time(),
            sequence=self._sequence_counter,
            payload=payload or {},
        )
        self._events.append(event)

        for listener in self._event_listeners:
            try:
                listener(event)
            except Exception:
                pass  # Listeners must not crash the core engine

        return event

    def emit_event(
        self,
        event_type: EventType,
        task_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None
    ) -> Event:
        """Public API to emit an event."""
        return self._emit(event_type, task_id=task_id, payload=payload)

    # -------------------------------------------------------------------------
    # Mission Lifecycle
    # -------------------------------------------------------------------------

    def approve_plan(self) -> None:
        """Approves the mission plan, moving state from DRAFTING to PLAN_APPROVED."""
        old_state = self._mission.state
        self._mission.transition_to(MissionState.PLAN_APPROVED)
        self._emit(
            EventType.MISSION_STATE_CHANGED,
            payload={"old_state": old_state.value, "new_state": self._mission.state.value}
        )

    def start_mission(self) -> None:
        """Starts mission execution."""
        if self._mission.state == MissionState.DRAFTING:
            self.approve_plan()

        old_state = self._mission.state
        self._mission.transition_to(MissionState.EXECUTING)
        self._emit(
            EventType.MISSION_STATE_CHANGED,
            payload={"old_state": old_state.value, "new_state": self._mission.state.value}
        )
        if self._scheduler is not None:
            self._scheduler.evaluate()

    def start(self) -> None:
        """Alias for start_mission."""
        self.start_mission()

    def pause_mission(self, reason: str = "") -> None:
        """Pauses mission execution."""
        old_state = self._mission.state
        self._mission.transition_to(MissionState.PAUSED, reason=reason)
        self._emit(
            EventType.MISSION_STATE_CHANGED,
            payload={"old_state": old_state.value, "new_state": self._mission.state.value, "reason": reason}
        )

    def resume_mission(self) -> None:
        """Resumes a paused mission."""
        old_state = self._mission.state
        self._mission.transition_to(MissionState.EXECUTING)
        self._emit(
            EventType.MISSION_STATE_CHANGED,
            payload={"old_state": old_state.value, "new_state": self._mission.state.value}
        )

    def start_integrating(self) -> None:
        """Transitions mission state to INTEGRATING."""
        old_state = self._mission.state
        self._mission.transition_to(MissionState.INTEGRATING)
        self._emit(
            EventType.MISSION_STATE_CHANGED,
            payload={"old_state": old_state.value, "new_state": self._mission.state.value}
        )

    def start_auditing(self) -> None:
        """Transitions mission state to AUDITING."""
        old_state = self._mission.state
        self._mission.transition_to(MissionState.AUDITING)
        self._emit(
            EventType.MISSION_STATE_CHANGED,
            payload={"old_state": old_state.value, "new_state": self._mission.state.value}
        )

    def _check_mission_completion(self) -> None:
        """
        Evaluates mission completion progression:
        EXECUTING -> INTEGRATING (if unmerged writes remain) -> AUDITING -> COMPLETED (via Tier 4 Audit).
        """
        all_passed = all(t.status.is_terminal and t.status.is_success for t in self._graph.all_tasks())
        if not (all_passed and self._graph.task_count() > 0):
            return

        # Check if any tasks are in branch mode with writes not yet merged
        has_unmerged_writes = any(
            t.workspace_mode == "branch" and t.write_set and t.status != TaskState.MERGED
            for t in self._graph.all_tasks()
        )

        if has_unmerged_writes:
            if self._mission.state == MissionState.EXECUTING:
                old_state = self._mission.state
                self._mission.transition_to(MissionState.INTEGRATING)
                self._emit(
                    EventType.MISSION_STATE_CHANGED,
                    payload={"old_state": old_state.value, "new_state": self._mission.state.value}
                )
            return

        # All writes merged (or no writes needed merge). Advance to AUDITING
        if self._mission.state in (MissionState.EXECUTING, MissionState.INTEGRATING):
            old_state = self._mission.state
            self._mission.transition_to(MissionState.AUDITING)
            self._emit(
                EventType.MISSION_STATE_CHANGED,
                payload={"old_state": old_state.value, "new_state": self._mission.state.value}
            )

        # Trigger automatic Victory Audit if enabled
        if self._auto_audit and self._mission.state == MissionState.AUDITING:
            self.run_victory_audit(
                required_artifacts=self._required_artifacts,
                acceptance_criteria=self._acceptance_criteria,
            )

    def run_victory_audit(
        self,
        required_artifacts: Optional[List[str]] = None,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
        verifier: Optional[Tier4VictoryAuditVerifier] = None,
    ) -> VictoryAuditResult:
        """
        Conducts Tier 4 Victory Audit on the mission.
        Evaluates whole-mission acceptance criteria, artifact presence, and regression status.
        Transitions AUDITING -> COMPLETED if audit passes,
        or AUDITING -> FAILED if audit fails.
        """
        if self._mission.state not in (MissionState.AUDITING, MissionState.EXECUTING, MissionState.INTEGRATING):
            raise InvalidStateTransitionError(
                f"Cannot run victory audit in state {self._mission.state.value}"
            )

        if self._mission.state != MissionState.AUDITING:
            old_st = self._mission.state
            self._mission.transition_to(MissionState.AUDITING)
            self._emit(
                EventType.MISSION_STATE_CHANGED,
                payload={"old_state": old_st.value, "new_state": MissionState.AUDITING.value}
            )

        req_arts = required_artifacts if required_artifacts is not None else self._required_artifacts
        crit = acceptance_criteria if acceptance_criteria is not None else self._acceptance_criteria

        if self._verification_engine is not None:
            audit_result = self._verification_engine.run_victory_audit(
                mission=self._mission,
                tasks=self._graph.all_tasks(),
                required_artifacts=req_arts,
                acceptance_criteria=crit,
            )
        else:
            audit_verifier = verifier or self._tier4_verifier or Tier4VictoryAuditVerifier()
            audit_result = audit_verifier.audit_mission(
                mission=self._mission,
                tasks=self._graph.all_tasks(),
                required_artifacts=req_arts,
                acceptance_criteria=crit,
            )

        if audit_result.passed:
            old_state = self._mission.state
            self._mission.transition_to(MissionState.COMPLETED)
            self._emit(
                EventType.MISSION_STATE_CHANGED,
                payload={
                    "old_state": old_state.value,
                    "new_state": self._mission.state.value,
                    "verdict": "VICTORY CONFIRMED",
                    "summary": audit_result.summary,
                }
            )
        else:
            old_state = self._mission.state
            self._mission.transition_to(MissionState.FAILED)
            self._emit(
                EventType.MISSION_STATE_CHANGED,
                payload={
                    "old_state": old_state.value,
                    "new_state": self._mission.state.value,
                    "reason": audit_result.summary,
                    "unresolved_failures": audit_result.unresolved_failures,
                }
            )

        return audit_result

    # -------------------------------------------------------------------------
    # Task Management
    # -------------------------------------------------------------------------

    def add_task(
        self,
        task_or_id: Optional[Union[Task, str]] = None,
        title: str = "",
        description: str = "",
        domain: str = "general",
        priority: float = 0.0,
        dependencies: Optional[Iterable[str]] = None,
        read_set: Optional[Iterable[str]] = None,
        write_set: Optional[Iterable[str]] = None,
        workspace_mode: str = "branch",
        metadata: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> Task:
        """
        Adds a new task to the mission graph.
        Accepts either a Task instance directly or individual fields.
        If the task has zero dependencies, it immediately becomes READY and is enqueued.
        """
        if isinstance(task_or_id, Task):
            task = task_or_id
        else:
            tid = task_id or (task_or_id if isinstance(task_or_id, str) else None)
            if not tid:
                raise ValueError("Must provide a task_id or Task instance")
            t_title = title or ""
            t_domain = domain
            t_workspace_mode = workspace_mode
            t_priority = priority
            task = Task(
                task_id=tid,
                mission_id=self._mission.mission_id,
                title=t_title,
                description=description,
                domain=t_domain,
                priority=t_priority,
                status=TaskState.PENDING,
                dependencies=set(dependencies or []),
                read_set=set(read_set or []),
                write_set=set(write_set or []),
                workspace_mode=t_workspace_mode,
                metadata=dict(metadata or {}),
            )

        self._graph.add_task(task)
        self._emit(
            EventType.TASK_CREATED,
            task_id=task.task_id,
            payload={
                "title": task.title,
                "domain": task.domain,
                "dependencies": sorted(list(task.dependencies)),
                "read_set": sorted(list(task.read_set)),
                "write_set": sorted(list(task.write_set)),
                "workspace_mode": task.workspace_mode,
            }
        )

        # Check initial readiness
        if self._resolver.is_task_ready(task.task_id):
            task.transition_to(TaskState.READY, reason="Zero initial dependencies")
            unlock_val = self._resolver.calculate_unlock_value(task.task_id)
            self._ready_queue.push(task, unlock_value=unlock_val)
            self._emit(
                EventType.TASK_READY,
                task_id=task.task_id,
                payload={"priority": task.priority, "unlock_value": unlock_val}
            )

        return task

    def add_dependency(self, dependent_id: str, prerequisite_id: str) -> None:
        """
        Adds a dependency edge: dependent_id depends on prerequisite_id.
        If dependent_id was previously queued in ready_queue but prerequisite is not PASSED,
        dependent_id is removed from ready_queue and reverted to PENDING.
        """
        dependent = self._graph.get_task(dependent_id)
        prerequisite = self._graph.get_task(prerequisite_id)

        self._graph.add_dependency(dependent_id, prerequisite_id)

        self._emit(
            EventType.GRAPH_MUTATED,
            task_id=dependent_id,
            payload={"action": "add_dependency", "prerequisite_id": prerequisite_id}
        )

        # Re-evaluate readiness of dependent
        if not prerequisite.status.is_success:
            if dependent.status == TaskState.READY:
                self._ready_queue.remove(dependent_id)
                dependent.status = TaskState.PENDING
                self._emit(
                    EventType.DEPENDENCY_INVALIDATED,
                    task_id=dependent_id,
                    payload={"unsatisfied_prerequisite": prerequisite_id}
                )

    def remove_dependency(self, dependent_id: str, prerequisite_id: str) -> bool:
        """
        Removes a dependency edge.
        If dependent becomes ready as a result, it is transitioned to READY and enqueued.
        """
        removed = self._graph.remove_dependency(dependent_id, prerequisite_id)
        if removed:
            self._emit(
                EventType.GRAPH_MUTATED,
                task_id=dependent_id,
                payload={"action": "remove_dependency", "prerequisite_id": prerequisite_id}
            )

            # Check if dependent is now ready
            if self._resolver.is_task_ready(dependent_id):
                task = self._graph.get_task(dependent_id)
                if task.status in (TaskState.PENDING, TaskState.BLOCKED):
                    task.transition_to(TaskState.READY, reason="Dependency removed")
                    unlock_val = self._resolver.calculate_unlock_value(dependent_id)
                    self._ready_queue.push(task, unlock_value=unlock_val)
                    self._emit(
                        EventType.TASK_READY,
                        task_id=dependent_id,
                        payload={"unlock_value": unlock_val}
                    )

        return removed

    # -------------------------------------------------------------------------
    # Scheduling & Readiness API
    # -------------------------------------------------------------------------

    def get_ready_tasks(self) -> List[Task]:
        """Returns snapshot of all currently ready tasks in priority order."""
        return TaskList(self._ready_queue.all_tasks())

    def pop_next_ready_task(self) -> Optional[Task]:
        """Pops and returns the highest priority ready task from the queue."""
        return self._ready_queue.pop_optional()

    def mark_task_assigned(self, task_id: str, worker_id: Optional[str] = None) -> Task:
        """
        Transitions task from READY to ASSIGNED.
        """
        task = self._graph.get_task(task_id)
        if task.status == TaskState.READY:
            task.transition_to(TaskState.ASSIGNED)
            if worker_id:
                task.assigned_worker_id = worker_id
            prof = getattr(task, "execution_profile", None)
            tier_val = getattr(prof, "tier", None)
            tier_str = getattr(tier_val, "value", str(tier_val)) if tier_val else None
            reason_str = getattr(prof, "reason", None) or getattr(prof, "routing_reason", None)
            is_reuse = False
            if worker_id:
                worker = self._workers.get(worker_id)
                if worker and worker.tasks_completed > 0:
                    is_reuse = True
            self._emit(
                EventType.TASK_ASSIGNED,
                task_id=task_id,
                payload={
                    "worker_id": worker_id,
                    "domain": task.domain,
                    "model_tier": tier_str,
                    "routing_reason": reason_str,
                    "is_reuse": is_reuse,
                }
            )
        return task

    def mark_task_started(self, task_id: str) -> Task:
        """
        Transitions task from READY or ASSIGNED to RUNNING.
        Removes task from ready queue if still present.
        """
        task = self._graph.get_task(task_id)

        if task.status not in (TaskState.READY, TaskState.ASSIGNED):
            raise TaskNotReadyError(
                f"Cannot start task '{task_id}': status is {task.status.value}, expected READY or ASSIGNED."
            )

        self._ready_queue.remove(task_id)
        task.transition_to(TaskState.RUNNING)

        if self._mission.state in (MissionState.DRAFTING, MissionState.PLAN_APPROVED):
            self._mission.state = MissionState.EXECUTING

        self._emit(
            EventType.TASK_STARTED,
            task_id=task_id,
            payload={"started_at": task.started_at}
        )
        return task

    def mark_task_verifying(self, task_id: str) -> Task:
        """Transitions task from RUNNING to VERIFYING."""
        task = self._graph.get_task(task_id)
        task.transition_to(TaskState.VERIFYING)
        self._emit(EventType.TASK_VERIFYING, task_id=task_id)
        return task

    def mark_task_completed(
        self,
        task_id: str,
        result: Optional[Dict[str, Any]] = None
    ) -> Tuple[Task, List[Task]]:
        """
        Marks task as PASSED.
        Evaluates downstream dependents and pushes newly ready tasks into the ReadyQueue.
        Returns tuple of (completed_task, list_of_newly_ready_tasks).
        """
        task = self._graph.get_task(task_id)
        task.result = result or {}
        task.transition_to(TaskState.PASSED)

        self._emit(
            EventType.TASK_COMPLETED,
            task_id=task_id,
            payload={"completed_at": task.completed_at, "result": task.result, "worker_id": task.assigned_worker_id}
        )

        # Release worker if held
        if task.assigned_worker_id:
            self._workers.release(task.assigned_worker_id, success=True)
            task.assigned_worker_id = None

        # Auto-merge if task modified files in a branch
        if task.write_set:
            if self._integration_manager is not None:
                self._integration_manager.enqueue(
                    task_id=task.task_id,
                    branch_name=getattr(task, "branch_name", f"branch-{task.task_id}"),
                    write_set=task.write_set,
                )
                self._integration_manager.process_next()
            if task.can_transition_to(TaskState.MERGED):
                task.transition_to(TaskState.MERGED)

        # Resolve newly ready dependents
        newly_ready = self._resolver.resolve_dependents_on_completion(task_id)

        for dep_task in newly_ready:
            unlock_val = self._resolver.calculate_unlock_value(dep_task.task_id)
            self._ready_queue.push(dep_task, unlock_value=unlock_val)
            self._emit(
                EventType.DEPENDENCY_SATISFIED,
                task_id=dep_task.task_id,
                payload={"satisfied_by": task_id}
            )
            self._emit(
                EventType.TASK_READY,
                task_id=dep_task.task_id,
                payload={"unlock_value": unlock_val, "priority": dep_task.priority}
            )

        # Check if all tasks in mission have completed successfully and trigger lifecycle
        self._check_mission_completion()

        return task, newly_ready

    def mark_task_merged(
        self,
        task_id: str,
        commit_id: Optional[str] = None
    ) -> Tuple[Task, List[Task]]:
        """
        Marks task as MERGED.
        Evaluates downstream dependents and pushes newly ready tasks into the ReadyQueue.
        Returns tuple of (merged_task, list_of_newly_ready_tasks).
        """
        task = self._graph.get_task(task_id)
        if task.status == TaskState.PASSED:
            task.transition_to(TaskState.MERGED)
        elif task.can_transition_to(TaskState.MERGED):
            task.transition_to(TaskState.MERGED)

        self._emit(
            EventType.TASK_STATE_CHANGED,
            task_id=task_id,
            payload={
                "old_state": TaskState.PASSED.value,
                "new_state": TaskState.MERGED.value,
                "commit_id": commit_id,
            }
        )

        # Resolve newly ready dependents
        newly_ready = self._resolver.resolve_dependents_on_completion(task_id)

        for dep_task in newly_ready:
            if not self._ready_queue.contains(dep_task.task_id):
                unlock_val = self._resolver.calculate_unlock_value(dep_task.task_id)
                self._ready_queue.push(dep_task, unlock_value=unlock_val)
                self._emit(
                    EventType.DEPENDENCY_SATISFIED,
                    task_id=dep_task.task_id,
                    payload={"satisfied_by": task_id}
                )
                self._emit(
                    EventType.TASK_READY,
                    task_id=dep_task.task_id,
                    payload={"unlock_value": unlock_val, "priority": dep_task.priority}
                )

        # Check if all tasks in mission have completed successfully and trigger lifecycle
        self._check_mission_completion()

        if self._scheduler is not None:
            self._scheduler.evaluate()

        return task, newly_ready

    def mark_task_failed(
        self,
        task_id: str,
        error: str = "",
        can_retry: Optional[bool] = None
    ) -> Task:
        """
        Marks task as FAILED (or RETRYING / READY if retry limit not exceeded).
        If permanently failed, transitions pending dependents to BLOCKED.
        """
        task = self._graph.get_task(task_id)
        task.error = error

        if can_retry is not None:
            should_retry = can_retry and (task.retry_count < task.max_retries)
        else:
            err_lower = error.lower()
            if "transient" in err_lower or "timeout" in err_lower:
                should_retry = task.retry_count < task.max_retries
            else:
                should_retry = False

        # Release worker if held
        if task.assigned_worker_id:
            self._workers.release(task.assigned_worker_id, success=False)
            task.assigned_worker_id = None

        # Release workspace if held
        if self._workspace_registry.is_locked(task_id):
            self._workspace_registry.release(task_id)

        if should_retry:
            if can_retry is True:
                task.transition_to(TaskState.RETRYING, reason=error)
                self._emit(
                    EventType.TASK_RETRYING,
                    task_id=task_id,
                    payload={"retry_count": task.retry_count, "error": error}
                )
            else:
                task.transition_to(TaskState.RETRYING, reason=error)
                task.transition_to(TaskState.READY, reason="Automatic retry")
                if not self._ready_queue.contains(task_id):
                    unlock_val = self._resolver.calculate_unlock_value(task_id)
                    self._ready_queue.push(task, unlock_value=unlock_val)
                self._emit(
                    EventType.TASK_RETRYING,
                    task_id=task_id,
                    payload={"retry_count": task.retry_count, "error": error}
                )
        else:
            task.transition_to(TaskState.FAILED, reason=error)
            self._emit(
                EventType.TASK_FAILED,
                task_id=task_id,
                payload={"error": error, "retries_exhausted": task.retry_count >= task.max_retries}
            )

            # Block dependent tasks
            newly_blocked = self._resolver.resolve_dependents_on_failure(task_id)
            for blocked_task in newly_blocked:
                self._emit(
                    EventType.TASK_STATE_CHANGED,
                    task_id=blocked_task.task_id,
                    payload={"old_state": TaskState.PENDING.value, "new_state": TaskState.BLOCKED.value, "blocked_by": task_id}
                )

        return task

    def retry_task(self, task_id: str) -> Task:
        """
        Transitions a RETRYING task to READY and re-enqueues it into the ready queue.
        """
        task = self._graph.get_task(task_id)
        if task.status != TaskState.RETRYING:
            raise InvalidStateTransitionError(
                f"Task '{task_id}' is in state {task.status.value}, expected RETRYING to retry"
            )
        task.transition_to(TaskState.READY, reason="Retry eligibility")
        unlock_val = self._resolver.calculate_unlock_value(task_id)
        self._ready_queue.push(task, unlock_value=unlock_val)
        self._emit(
            EventType.TASK_READY,
            task_id=task_id,
            payload={"unlock_value": unlock_val, "priority": task.priority, "is_retry": True}
        )
        if self._scheduler is not None:
            self._scheduler.evaluate()
        return task

    def mark_task_cancelled(self, task_id: str, reason: str = "") -> List[Task]:
        """
        Cancels task_id and all of its transitive downstream dependents.
        Removes any of them from the ready queue.
        Safely releases workspace ownership and cleans worktrees.
        Returns list of all cancelled tasks.
        """
        if not self._graph.has_task(task_id):
            raise TaskNotFoundError(f"Cannot cancel non-existent task '{task_id}'.")

        descendants = self._graph.get_descendants(task_id)
        target_ids = [task_id] + sorted(list(descendants))

        cancelled_tasks: List[Task] = []

        for tid in target_ids:
            t = self._graph.get_task(tid)
            if t.status != TaskState.CANCELLED:
                self._ready_queue.remove(tid)
                if t.can_transition_to(TaskState.CANCELLED):
                    t.transition_to(TaskState.CANCELLED, reason=reason)
                else:
                    t.status = TaskState.CANCELLED

                # Clean release workspace ownership and worktree
                if self._workspace_registry and self._workspace_registry.has_active_ownership(tid):
                    self._workspace_registry.release(
                        tid,
                        state=WorkspaceReleaseState.CANCELLED,
                        reason=reason or "Task cancelled"
                    )

                if self._worktree_adapter and t.workspace_path:
                    try:
                        self._worktree_adapter.cleanup_workspace(t.workspace_path, t.branch_name)
                    except Exception:
                        pass

                self._emit(
                    EventType.TASK_CANCELLED,
                    task_id=tid,
                    payload={"reason": reason}
                )
                cancelled_tasks.append(t)

        return cancelled_tasks

    # -------------------------------------------------------------------------
    # Dynamic Mutation
    # -------------------------------------------------------------------------

    def invalidate_task(self, task_id: str, reason: str = "") -> List[str]:
        """
        Invalidates a task and all downstream descendants.
        Tasks are reset to PENDING and removed from ready queue.
        Readiness is re-evaluated across the graph.
        Returns list of affected task IDs.
        """
        affected_ids = self._mutations.invalidate_task(task_id, reason=reason)

        for tid in affected_ids:
            self._ready_queue.remove(tid)
            self._emit(
                EventType.GRAPH_MUTATED,
                task_id=tid,
                payload={"action": "invalidate_task", "reason": reason}
            )

        # Re-resolve ready tasks for affected graph
        ready_tasks = self._resolver.resolve_all_ready()
        for r_task in ready_tasks:
            if not self._ready_queue.contains(r_task.task_id):
                unlock_val = self._resolver.calculate_unlock_value(r_task.task_id)
                self._ready_queue.push(r_task, unlock_value=unlock_val)
                self._emit(
                    EventType.TASK_READY,
                    task_id=r_task.task_id,
                    payload={"unlock_value": unlock_val}
                )

        return affected_ids
