"""
Adaptive Orchestrator v5 - Telemetry & Observability Data Models.
Deterministic, structured execution metrics without verbose raw log pollution.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MissionMetrics:
    """Mission-level metrics."""
    mission_id: str = ""
    title: str = ""
    status: str = "DRAFTING"
    start_time: float = 0.0
    end_time: Optional[float] = None
    duration_seconds: float = 0.0
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    retried_tasks: int = 0
    total_verifications: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "title": self.title,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 4),
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "retried_tasks": self.retried_tasks,
            "total_verifications": self.total_verifications,
        }


@dataclass
class SchedulerMetrics:
    """Scheduler, ready queue, and AIMD metrics."""
    dispatch_count: int = 0
    total_queue_wait_seconds: float = 0.0
    average_queue_wait_seconds: float = 0.0
    peak_active_concurrency: int = 0
    current_active_concurrency: int = 0
    initial_capacity: int = 2
    peak_capacity: int = 2
    current_capacity: int = 2
    capacity_increases: int = 0
    capacity_decreases: int = 0
    scheduler_decisions: int = 0

    @property
    def peak_concurrency(self) -> int:
        return self.peak_active_concurrency

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dispatch_count": self.dispatch_count,
            "total_queue_wait_seconds": round(self.total_queue_wait_seconds, 4),
            "average_queue_wait_seconds": round(self.average_queue_wait_seconds, 4),
            "peak_active_concurrency": self.peak_active_concurrency,
            "current_active_concurrency": self.current_active_concurrency,
            "initial_capacity": self.initial_capacity,
            "peak_capacity": self.peak_capacity,
            "current_capacity": self.current_capacity,
            "capacity_increases": self.capacity_increases,
            "capacity_decreases": self.capacity_decreases,
            "scheduler_decisions": self.scheduler_decisions,
        }


@dataclass
class WorkerStats:
    """Individual worker telemetry summary."""
    worker_id: str
    tasks_completed: int = 0
    tasks_failed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
        }


@dataclass
class WorkerMetricsReport:
    """Worker pool, reuse, and domain affinity metrics."""
    registered_workers: int = 0
    active_workers: int = 0
    idle_workers: int = 0
    retired_workers: int = 0
    worker_reuse_count: int = 0
    worker_failures: int = 0
    average_execution_seconds: float = 0.0
    tasks_per_worker: Dict[str, int] = field(default_factory=dict)
    domain_distribution: Dict[str, int] = field(default_factory=dict)
    workers: Dict[str, WorkerStats] = field(default_factory=dict)

    @property
    def total_worker_reuses(self) -> int:
        return self.worker_reuse_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "registered_workers": self.registered_workers,
            "active_workers": self.active_workers,
            "idle_workers": self.idle_workers,
            "retired_workers": self.retired_workers,
            "worker_reuse_count": self.worker_reuse_count,
            "worker_failures": self.worker_failures,
            "average_execution_seconds": round(self.average_execution_seconds, 4),
            "tasks_per_worker": dict(self.tasks_per_worker),
            "domain_distribution": dict(self.domain_distribution),
            "workers": {wid: w.to_dict() for wid, w in self.workers.items()},
        }


@dataclass
class WorkspaceMetrics:
    """Workspace exclusivity, collision, and integration queue metrics."""
    workspace_acquisitions: int = 0
    workspace_releases: int = 0
    workspace_conflicts: int = 0
    worktrees_created: int = 0
    worktrees_cleaned: int = 0
    merges_requested: int = 0
    merges_completed: int = 0
    merges_failed: int = 0
    total_merge_wait_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_acquisitions": self.workspace_acquisitions,
            "workspace_releases": self.workspace_releases,
            "workspace_conflicts": self.workspace_conflicts,
            "worktrees_created": self.worktrees_created,
            "worktrees_cleaned": self.worktrees_cleaned,
            "merges_requested": self.merges_requested,
            "merges_completed": self.merges_completed,
            "merges_failed": self.merges_failed,
            "total_merge_wait_seconds": round(self.total_merge_wait_seconds, 4),
        }


@dataclass
class VerificationMetrics:
    """Incremental verification, tier results, and local repair metrics."""
    tier1_attempts: int = 0
    tier1_passes: int = 0
    tier1_fails: int = 0
    tier2_attempts: int = 0
    tier2_passes: int = 0
    tier2_fails: int = 0
    tier3_attempts: int = 0
    tier3_passes: int = 0
    tier3_fails: int = 0
    tier4_attempts: int = 0
    tier4_passes: int = 0
    tier4_fails: int = 0
    repair_attempts: int = 0
    repair_successes: int = 0
    repair_failures: int = 0
    retry_exhaustions: int = 0

    @property
    def tier1_passed(self) -> int:
        return self.tier1_passes

    @property
    def tier1_failed(self) -> int:
        return self.tier1_fails

    @property
    def tier2_passed(self) -> int:
        return self.tier2_passes

    @property
    def tier2_failed(self) -> int:
        return self.tier2_fails

    @property
    def tier3_passed(self) -> int:
        return self.tier3_passes

    @property
    def tier3_failed(self) -> int:
        return self.tier3_fails

    @property
    def tier4_audits_passed(self) -> int:
        return self.tier4_passes

    @property
    def tier4_audits_failed(self) -> int:
        return self.tier4_fails

    @property
    def repair_success_rate(self) -> float:
        if self.repair_attempts == 0:
            return 0.0
        return self.repair_successes / self.repair_attempts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier1_attempts": self.tier1_attempts,
            "tier1_passes": self.tier1_passes,
            "tier1_fails": self.tier1_fails,
            "tier2_attempts": self.tier2_attempts,
            "tier2_passes": self.tier2_passes,
            "tier2_fails": self.tier2_fails,
            "tier3_attempts": self.tier3_attempts,
            "tier3_passes": self.tier3_passes,
            "tier3_fails": self.tier3_fails,
            "tier4_attempts": self.tier4_attempts,
            "tier4_passes": self.tier4_passes,
            "tier4_fails": self.tier4_fails,
            "repair_attempts": self.repair_attempts,
            "repair_successes": self.repair_successes,
            "repair_failures": self.repair_failures,
            "repair_success_rate": round(self.repair_success_rate, 4),
            "retry_exhaustions": self.retry_exhaustions,
        }


@dataclass
class RoutingMetrics:
    """Model routing and tier selection metrics."""
    fast_executions: int = 0
    pro_executions: int = 0
    routing_reasons: Dict[str, int] = field(default_factory=dict)

    @property
    def routing_decisions(self) -> int:
        return self.fast_executions + self.pro_executions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fast_executions": self.fast_executions,
            "pro_executions": self.pro_executions,
            "total_routed": self.fast_executions + self.pro_executions,
            "routing_reasons": dict(self.routing_reasons),
        }


@dataclass
class TelemetryReport:
    """
    Comprehensive mission execution telemetry artifact.
    Compact, structured, deterministic JSON.
    """
    version: str = "5.0"
    generated_at: float = field(default_factory=time.time)
    mission: MissionMetrics = field(default_factory=MissionMetrics)
    scheduler: SchedulerMetrics = field(default_factory=SchedulerMetrics)
    workers: WorkerMetricsReport = field(default_factory=WorkerMetricsReport)
    workspace: WorkspaceMetrics = field(default_factory=WorkspaceMetrics)
    verification: VerificationMetrics = field(default_factory=VerificationMetrics)
    routing: RoutingMetrics = field(default_factory=RoutingMetrics)

    @property
    def mission_id(self) -> str:
        return self.mission.mission_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "mission_id": self.mission_id,
            "generated_at": self.generated_at,
            "mission": self.mission.to_dict(),
            "scheduler": self.scheduler.to_dict(),
            "workers": self.workers.to_dict(),
            "workspace": self.workspace.to_dict(),
            "verification": self.verification.to_dict(),
            "routing": self.routing.to_dict(),
        }

    def save(self, file_path: str = "telemetry.json") -> str:
        """Saves telemetry report to disk as clean formatted JSON."""
        abs_path = os.path.abspath(file_path)
        d = os.path.dirname(abs_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        return abs_path
