"""
Adaptive Orchestrator v5 - Telemetry Collector.
Consumes real-time structured engine events and computes mission performance metrics.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from orchestrator.models import Event, EventType, MissionState, TaskState
from orchestrator.telemetry.models import (
    MissionMetrics,
    RoutingMetrics,
    SchedulerMetrics,
    TelemetryReport,
    VerificationMetrics,
    WorkerMetricsReport,
    WorkerStats,
    WorkspaceMetrics,
)

if TYPE_CHECKING:
    from orchestrator.engine import MissionEngine


class TelemetryCollector:
    """
    Subscribes to MissionEngine event stream to compute comprehensive, deterministic
    telemetry without modifying execution state.
    """

    def __init__(
        self,
        engine: Optional[MissionEngine] = None,
        mission_id: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> None:
        self.report = TelemetryReport()
        if mission_id:
            self.report.mission.mission_id = mission_id
        self._engine = engine
        self._output_path = output_path

        # Tracking maps for intermediate durations
        self._task_ready_times: Dict[str, float] = {}
        self._task_start_times: Dict[str, float] = {}
        self._task_worker_map: Dict[str, str] = {}
        self._merge_ready_times: Dict[str, float] = {}
        self._execution_durations: List[float] = []
        self._queue_wait_durations: List[float] = []

        if engine is not None:
            self.attach_to_engine(engine)

    def on_event(self, event: Event) -> None:
        """Alias for ingest_event."""
        self.ingest_event(event)

    def get_report(self) -> TelemetryReport:
        """Alias for generate_report."""
        return self.generate_report()

    def save(self, file_path: Optional[str] = None) -> str:
        """Alias for save_report."""
        target = file_path or self._output_path or "telemetry.json"
        return self.save_report(target)

    def attach_to_engine(self, engine: MissionEngine) -> None:
        """Attaches telemetry collector to engine event stream."""
        self._engine = engine
        self.report.mission.mission_id = engine.mission.mission_id
        self.report.mission.title = engine.mission.title
        self.report.mission.status = engine.mission.state.value
        self.report.mission.start_time = engine.mission.created_at

        engine.subscribe(self.ingest_event)

    def ingest_event(self, event: Event) -> None:
        """Processes an incoming internal engine event to update telemetry metrics."""
        etype = event.event_type
        payload = event.payload or {}
        tid = event.task_id
        now = event.timestamp or time.time()

        # 1. Mission Lifecycle
        if etype == EventType.MISSION_CREATED:
            self.report.mission.mission_id = event.mission_id
            self.report.mission.title = payload.get("title", "")
            self.report.mission.status = payload.get("state", "DRAFTING")
            self.report.mission.start_time = now

        elif etype == EventType.MISSION_STATE_CHANGED:
            new_st = payload.get("new_state", "")
            self.report.mission.status = new_st
            if new_st in (MissionState.COMPLETED.value, MissionState.FAILED.value, MissionState.CANCELLED.value):
                self.report.mission.end_time = now
                if self.report.mission.start_time > 0:
                    self.report.mission.duration_seconds = now - self.report.mission.start_time
                if self._output_path:
                    self.save(self._output_path)

        # 2. Task Lifecycle & Queue Waits
        elif etype == EventType.TASK_CREATED:
            self.report.mission.total_tasks += 1

        elif etype == EventType.TASK_READY:
            if tid:
                self._task_ready_times[tid] = now

        elif etype == EventType.TASK_ASSIGNED:
            self.report.scheduler.dispatch_count += 1
            wid = payload.get("worker_id")
            domain = payload.get("domain", "general")
            is_reuse = payload.get("is_reuse", False)

            if is_reuse:
                self.report.workers.worker_reuse_count += 1

            if wid:
                if wid not in self.report.workers.workers:
                    self.report.workers.workers[wid] = WorkerStats(worker_id=wid)
                if tid:
                    self._task_worker_map[tid] = wid
                self.report.workers.tasks_per_worker[wid] = (
                    self.report.workers.tasks_per_worker.get(wid, 0) + 1
                )
            self.report.workers.domain_distribution[domain] = (
                self.report.workers.domain_distribution.get(domain, 0) + 1
            )

            # Routing metrics
            m_tier = payload.get("model_tier")
            if m_tier == "FAST":
                self.report.routing.fast_executions += 1
            elif m_tier == "PRO":
                self.report.routing.pro_executions += 1

            reason = payload.get("routing_reason")
            if reason:
                self.report.routing.routing_reasons[reason] = (
                    self.report.routing.routing_reasons.get(reason, 0) + 1
                )

            # Calculate queue wait
            if tid and tid in self._task_ready_times:
                wait_time = max(0.0, now - self._task_ready_times[tid])
                self._queue_wait_durations.append(wait_time)
                self.report.scheduler.total_queue_wait_seconds += wait_time
                self.report.scheduler.average_queue_wait_seconds = (
                    self.report.scheduler.total_queue_wait_seconds / len(self._queue_wait_durations)
                )

        elif etype == EventType.TASK_STARTED:
            if tid:
                self._task_start_times[tid] = now
            self.report.scheduler.current_active_concurrency += 1
            if self.report.scheduler.current_active_concurrency > self.report.scheduler.peak_active_concurrency:
                self.report.scheduler.peak_active_concurrency = self.report.scheduler.current_active_concurrency

        elif etype in (EventType.TASK_COMPLETED, EventType.TASK_PASSED):
            self.report.mission.completed_tasks += 1
            if self.report.scheduler.current_active_concurrency > 0:
                self.report.scheduler.current_active_concurrency -= 1

            wid = payload.get("worker_id") or (self._task_worker_map.get(tid) if tid else None)
            if wid:
                if wid not in self.report.workers.workers:
                    self.report.workers.workers[wid] = WorkerStats(worker_id=wid)
                self.report.workers.workers[wid].tasks_completed += 1

            if tid and tid in self._task_start_times:
                dur = max(0.0, now - self._task_start_times[tid])
                self._execution_durations.append(dur)
                self.report.workers.average_execution_seconds = (
                    sum(self._execution_durations) / len(self._execution_durations)
                )

        elif etype == EventType.TASK_FAILED:
            self.report.mission.failed_tasks += 1
            if self.report.scheduler.current_active_concurrency > 0:
                self.report.scheduler.current_active_concurrency -= 1
            if payload.get("retries_exhausted", False):
                self.report.verification.retry_exhaustions += 1
            wid = payload.get("worker_id") or (self._task_worker_map.get(tid) if tid else None)
            if wid:
                if wid not in self.report.workers.workers:
                    self.report.workers.workers[wid] = WorkerStats(worker_id=wid)
                self.report.workers.workers[wid].tasks_failed += 1

        elif etype == EventType.TASK_RETRYING:
            self.report.mission.retried_tasks += 1
            if self.report.scheduler.current_active_concurrency > 0:
                self.report.scheduler.current_active_concurrency -= 1

        # 3. Scheduler & AIMD Capacity
        elif etype == EventType.CAPACITY_CHANGED:
            old_cap = payload.get("old_capacity", 2)
            new_cap = payload.get("new_capacity", 2)
            self.report.scheduler.current_capacity = new_cap
            self.report.scheduler.scheduler_decisions += 1
            if new_cap > self.report.scheduler.peak_capacity:
                self.report.scheduler.peak_capacity = new_cap
            if new_cap > old_cap:
                self.report.scheduler.capacity_increases += 1
            elif new_cap < old_cap:
                self.report.scheduler.capacity_decreases += 1

        # 4. Worker Lifecycle
        elif etype == EventType.WORKER_REGISTERED:
            self.report.workers.registered_workers += 1
            self.report.workers.idle_workers += 1
            wid = payload.get("worker_id")
            if wid and wid not in self.report.workers.workers:
                self.report.workers.workers[wid] = WorkerStats(worker_id=wid)

        elif etype == EventType.WORKER_BUSY:
            if self.report.workers.idle_workers > 0:
                self.report.workers.idle_workers -= 1
            self.report.workers.active_workers += 1

        elif etype == EventType.WORKER_IDLE:
            if self.report.workers.active_workers > 0:
                self.report.workers.active_workers -= 1
            self.report.workers.idle_workers += 1

        elif etype == EventType.WORKER_RETIRED:
            if self.report.workers.idle_workers > 0:
                self.report.workers.idle_workers -= 1
            self.report.workers.retired_workers += 1

        elif etype == EventType.WORKER_FAILED:
            self.report.workers.worker_failures += 1
            if self.report.workers.active_workers > 0:
                self.report.workers.active_workers -= 1

        # 5. Workspace & Merge Queue
        elif etype == EventType.WORKSPACE_ACQUIRED:
            self.report.workspace.workspace_acquisitions += 1
            if payload.get("workspace_mode") == "branch":
                self.report.workspace.worktrees_created += 1

        elif etype == EventType.WORKSPACE_RELEASED:
            self.report.workspace.workspace_releases += 1
            if payload.get("cleaned_up", False):
                self.report.workspace.worktrees_cleaned += 1

        elif etype == EventType.WORKSPACE_CONFLICT:
            self.report.workspace.workspace_conflicts += 1

        elif etype == EventType.MERGE_READY:
            self.report.workspace.merges_requested += 1
            if tid:
                self._merge_ready_times[tid] = now

        elif etype == EventType.MERGE_COMPLETED:
            self.report.workspace.merges_completed += 1
            if tid and tid in self._merge_ready_times:
                self.report.workspace.total_merge_wait_seconds += max(0.0, now - self._merge_ready_times[tid])

        elif etype == EventType.MERGE_FAILED:
            self.report.workspace.merges_failed += 1

        # 6. Verification Tiers
        elif etype == EventType.VERIFICATION_STARTED:
            self.report.mission.total_verifications += 1

        elif etype == EventType.VERIFICATION_PASSED:
            tier = payload.get("tier", "")
            if "TIER_1" in tier:
                self.report.verification.tier1_attempts += 1
                self.report.verification.tier1_passes += 1
            elif "TIER_2" in tier:
                self.report.verification.tier2_attempts += 1
                self.report.verification.tier2_passes += 1
            elif "TIER_3" in tier:
                self.report.verification.tier3_attempts += 1
                self.report.verification.tier3_passes += 1

        elif etype == EventType.VERIFICATION_FAILED:
            tier = payload.get("tier", "")
            if "TIER_1" in tier:
                self.report.verification.tier1_attempts += 1
                self.report.verification.tier1_fails += 1
            elif "TIER_2" in tier:
                self.report.verification.tier2_attempts += 1
                self.report.verification.tier2_fails += 1
            elif "TIER_3" in tier:
                self.report.verification.tier3_attempts += 1
                self.report.verification.tier3_fails += 1

        elif etype == EventType.TIER3_CHALLENGE_STARTED:
            self.report.verification.tier3_attempts += 1

        elif etype == EventType.TIER3_CHALLENGE_PASSED:
            self.report.verification.tier3_passes += 1

        elif etype == EventType.TIER3_CHALLENGE_FAILED:
            self.report.verification.tier3_fails += 1

        elif etype == EventType.TIER4_AUDIT_STARTED:
            self.report.verification.tier4_attempts += 1

        elif etype == EventType.TIER4_AUDIT_PASSED:
            self.report.verification.tier4_passes += 1

        elif etype == EventType.TIER4_AUDIT_FAILED:
            self.report.verification.tier4_fails += 1

        # 7. Local Repair Loop
        elif etype == EventType.REPAIR_REQUESTED:
            self.report.verification.repair_attempts += 1

        elif etype == EventType.REPAIR_COMPLETED:
            self.report.verification.repair_successes += 1

        elif etype == EventType.REPAIR_FAILED:
            self.report.verification.repair_failures += 1

    def generate_report(self) -> TelemetryReport:
        """Returns the accumulated telemetry report."""
        if self.report.mission.end_time is None and self.report.mission.start_time > 0:
            self.report.mission.duration_seconds = time.time() - self.report.mission.start_time
        return self.report

    def save_report(self, file_path: str = "telemetry.json") -> str:
        """Exports the accumulated telemetry report to disk."""
        return self.generate_report().save(file_path)
