"""
Adaptive Orchestrator v5 - Persistence Manager.
Provides atomic file persistence, crash recovery, interrupted task resolution,
and stale workspace lock neutralization.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from orchestrator.exceptions import (
    AdaptiveOrchestratorError,
    InvalidStateTransitionError,
    TaskNotFoundError,
)
from orchestrator.models import Event, EventType, MissionState, Task, TaskState
from orchestrator.persistence.checkpoint import CheckpointPolicy
from orchestrator.persistence.models import (
    CheckpointTrigger,
    InterruptedTaskRecovery,
    RecoveryReport,
    SerializedMissionState,
)
from orchestrator.workers.models import Worker, WorkerState

if TYPE_CHECKING:
    from orchestrator.engine import MissionEngine
    from orchestrator.integration.manager import IntegrationManager
    from orchestrator.scheduler.scheduler import EventDrivenScheduler
    from orchestrator.verification.engine import VerificationEngine
    from orchestrator.workers.registry import WorkerRegistry
    from orchestrator.workspace.adapter import WorktreeAdapter
    from orchestrator.workspace.registry import WorkspaceRegistry


def atomic_write_json(arg1: Any, arg2: Any) -> None:
    """
    Atomically writes a dictionary as JSON to target_path.
    Accepts either (data, target_path) or (target_path, data).
    Writes to a temporary file in the same directory, flushes, fsyncs,
    and atomically replaces the target file to prevent corruptions during crash.
    """
    if isinstance(arg1, (str, bytes, os.PathLike)) and not isinstance(arg2, (str, bytes, os.PathLike)):
        target_path = str(arg1)
        data = arg2
    else:
        data = arg1
        target_path = str(arg2)

    abs_target = os.path.abspath(target_path)
    target_dir = os.path.dirname(abs_target)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)

    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=target_dir,
            delete=False,
            encoding="utf-8",
            suffix=".tmp"
        ) as f:
            temp_file = f.name
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_file, abs_target)
    except Exception:
        if temp_file and os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except OSError:
                pass
        raise


class PersistenceManager:
    """
    Manages durable mission state persistence and safe crash recovery.
    """

    def __init__(
        self,
        policy: Optional[CheckpointPolicy] = None,
        state_file_path: Optional[str] = None,
        checkpoint_file: Optional[str] = None,
    ) -> None:
        target = state_file_path or checkpoint_file
        if policy is not None:
            self.policy = policy
            if target:
                self.policy.checkpoint_file = target
        else:
            self.policy = CheckpointPolicy(checkpoint_file=target or "mission_dag.json")
        self.state_file_path = self.policy.checkpoint_file
        self._engine: Optional[MissionEngine] = None

    def attach_to_engine(self, engine: MissionEngine) -> None:
        """Attaches persistence manager to automatically checkpoint based on policy."""
        self._engine = engine
        engine.subscribe(self._handle_engine_event)

    def _handle_engine_event(self, event: Event) -> None:
        """Event listener that triggers automatic atomic checkpoints."""
        if not self.policy.enabled or self._engine is None:
            return

        trigger = self.policy.map_event_to_trigger(event)
        if trigger and self.policy.should_checkpoint(trigger):
            try:
                self.save_mission(
                    self._engine,
                    file_path=self.policy.checkpoint_file,
                    trigger=trigger,
                )
            except Exception:
                pass  # Checkpoint errors must not crash execution

    def save_mission(
        self,
        engine: MissionEngine,
        file_path: Optional[str] = None,
        trigger: CheckpointTrigger = CheckpointTrigger.EXPLICIT,
    ) -> SerializedMissionState:
        """
        Captures serializable snapshot of mission engine and writes atomically to disk.
        Returns the SerializedMissionState object.
        """
        target_path = file_path or self.state_file_path or self.policy.checkpoint_file

        tasks_data: Dict[str, Dict[str, Any]] = {}
        deps_data: Dict[str, List[str]] = {}

        for task in engine.graph.all_tasks():
            t_dict = task.to_dict()
            t_dict["dependencies"] = sorted(list(task.dependencies))
            tasks_data[task.task_id] = t_dict
            deps_data[task.task_id] = sorted(list(task.dependencies))

        workers_data: Dict[str, Dict[str, Any]] = {}
        if hasattr(engine, "workers") and engine.workers:
            for worker in engine.workers.all_workers():
                workers_data[worker.worker_id] = worker.to_dict()

        events_data = [e.to_dict() for e in engine.get_events()]

        state = SerializedMissionState(
            mission=engine.mission.to_dict(),
            tasks=tasks_data,
            dependencies=deps_data,
            workers=workers_data,
            events=events_data,
            metadata={
                "sequence_counter": engine._sequence_counter,
                "saved_at_mission_state": engine.mission.state.value,
            },
            trigger=trigger.value,
        )

        atomic_write_json(state.to_dict(), target_path)

        if hasattr(engine, "emit_event"):
            engine.emit_event(
                EventType.CHECKPOINT_SAVED,
                payload={
                    "file_path": target_path,
                    "trigger": trigger.value,
                    "task_count": len(tasks_data),
                    "checkpoint_id": state.checkpoint_id,
                }
            )

        return state

    def load_mission(self, file_path: Optional[str] = None) -> SerializedMissionState:
        """
        Loads and validates a persisted mission state file.
        """
        target_path = file_path or self.state_file_path or self.policy.checkpoint_file
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"Persisted mission state file not found: '{target_path}'")

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid mission checkpoint JSON in '{target_path}': {str(e)}") from e

        return SerializedMissionState.from_dict(data)

    def restore_mission_engine(
        self,
        file_path: Optional[str] = None,
        worker_registry: Optional[WorkerRegistry] = None,
        scheduler: Optional[EventDrivenScheduler] = None,
        workspace_registry: Optional[WorkspaceRegistry] = None,
        worktree_adapter: Optional[WorktreeAdapter] = None,
        integration_manager: Optional[IntegrationManager] = None,
        verification_engine: Optional[VerificationEngine] = None,
    ) -> Tuple[MissionEngine, RecoveryReport]:
        """
        Restores a MissionEngine from persisted disk state.
        Performs crash recovery:
          1. Validates mission state.
          2. Restores tasks, states, and graph topology.
          3. Classifies and recovers interrupted tasks (RUNNING/VERIFYING/ASSIGNED -> READY/FAILED).
          4. Neutralizes stale workspace locks to prevent deadlocks.
          5. Re-derives ready queue.
          6. Emits MISSION_RECOVERED.
        """
        from orchestrator.engine import MissionEngine

        target_path = file_path or self.state_file_path or self.policy.checkpoint_file
        state = self.load_mission(target_path)
        m_dict = state.mission
        mission_id = m_dict.get("mission_id", "restored_mission")
        title = m_dict.get("title", "Restored Mission")
        desc = m_dict.get("description", "")
        meta = m_dict.get("metadata", {})

        engine = MissionEngine(
            mission_id=mission_id,
            title=title,
            description=desc,
            metadata=meta,
            worker_registry=worker_registry,
            scheduler=scheduler,
            workspace_registry=workspace_registry,
            worktree_adapter=worktree_adapter,
            integration_manager=integration_manager,
            verification_engine=verification_engine,
        )

        # Restore mission state
        try:
            raw_state = m_dict.get("state", MissionState.DRAFTING.value)
            engine.mission.state = MissionState(raw_state)
        except Exception:
            engine.mission.state = MissionState.EXECUTING

        # Track interrupted tasks
        interrupted_recoveries: List[InterruptedTaskRecovery] = []

        # 1. Restore Tasks
        task_list = list(state.tasks.values()) if isinstance(state.tasks, dict) else state.tasks
        for t_data in task_list:
            tid = t_data["task_id"]
            raw_status = t_data.get("status", TaskState.PENDING.value)
            prior_state = TaskState(raw_status)

            # Classify interrupted tasks
            recovered_state = prior_state
            recovery_reason = ""
            stale_worker = t_data.get("assigned_worker_id")
            stale_workspace = t_data.get("workspace_path")

            retry_count = int(t_data.get("retry_count", 0))
            max_retries = int(t_data.get("max_retries", 2))

            if prior_state in (TaskState.RUNNING, TaskState.VERIFYING, TaskState.ASSIGNED):
                if retry_count < max_retries:
                    recovered_state = TaskState.READY
                    retry_count += 1
                    recovery_reason = (
                        f"Process interrupted while in {prior_state.value}. "
                        f"Safely reverted to READY (retries: {retry_count}/{max_retries})."
                    )
                else:
                    recovered_state = TaskState.FAILED
                    recovery_reason = (
                        f"Process interrupted while in {prior_state.value} and retries exhausted ({retry_count}/{max_retries})."
                    )

                interrupted_recoveries.append(
                    InterruptedTaskRecovery(
                        task_id=tid,
                        prior_state=prior_state.value,
                        recovered_state=recovered_state.value,
                        reason=recovery_reason,
                    )
                )
                # Clear stale execution runtime links
                stale_worker = None
                stale_workspace = None

            task = Task(
                task_id=tid,
                mission_id=mission_id,
                title=t_data.get("title", ""),
                description=t_data.get("description", ""),
                status=recovered_state,
                domain=t_data.get("domain", "general"),
                dependencies=set(t_data.get("dependencies", [])),
                dependents=set(t_data.get("dependents", [])),
                priority=float(t_data.get("priority", 0.0)),
                read_set=set(t_data.get("read_set", [])),
                write_set=set(t_data.get("write_set", [])),
                workspace_mode=t_data.get("workspace_mode", "branch"),
                assigned_worker_id=stale_worker,
                workspace_path=stale_workspace,
                branch_name=t_data.get("branch_name"),
                result=t_data.get("result"),
                error=t_data.get("error") if recovered_state != TaskState.READY else None,
                created_at=float(t_data.get("created_at", 0.0)),
                started_at=float(t_data.get("started_at")) if t_data.get("started_at") else None,
                completed_at=float(t_data.get("completed_at")) if t_data.get("completed_at") else None,
                retry_count=retry_count,
                max_retries=int(t_data.get("max_retries", 2)),
                metadata=dict(t_data.get("metadata", {})),
            )
            engine.graph.add_task(task)

        # 2. Re-establish dependency edges
        for dependent_id, prereqs in state.dependencies.items():
            for prereq_id in prereqs:
                if engine.graph.has_task(dependent_id) and engine.graph.has_task(prereq_id):
                    engine.graph.add_dependency(dependent_id, prereq_id)

        # 2.5 Restore Workers and detect stale native sessions
        if hasattr(state, "workers") and state.workers and engine.workers:
            for w_id, w_data in state.workers.items():
                if not engine.workers.has_worker(w_id):
                    restored_worker = Worker.from_dict(w_data)
                    # Detect stale live native session:
                    # Stale native identity must not silently become an active healthy worker without verified reattachment
                    if restored_worker.conversation_id:
                        restored_worker.current_task_id = None
                        restored_worker.state = WorkerState.IDLE
                        restored_worker.metadata["restored_from_checkpoint"] = True
                        restored_worker.metadata["stale_session_detected"] = True
                    engine.workers.register_worker(restored_worker)

        # 3. Neutralize stale workspace locks
        # Stale persisted active workspace ownership locks are deliberately NOT restored.
        # This guarantees that stale crashes do not deadlock new dispatches.
        cleared_locks_count = len(engine.workspace_registry.get_active_records())

        # 4. Derive and re-populate Ready Queue
        ready_count = 0
        for task in engine.graph.all_tasks():
            if task.status == TaskState.READY:
                unlock_val = engine._resolver.calculate_unlock_value(task.task_id)
                engine._ready_queue.push(task, unlock_value=unlock_val)
                ready_count += 1

        report = RecoveryReport(
            mission_id=mission_id,
            recovered_tasks_count=engine.graph.task_count(),
            interrupted_tasks=interrupted_recoveries,
            active_locks_cleared=cleared_locks_count,
            ready_queue_size=ready_count,
        )

        engine.emit_event(
            EventType.MISSION_RECOVERED,
            payload=report.to_dict()
        )

        return engine, report
