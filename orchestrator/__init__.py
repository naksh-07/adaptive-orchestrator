"""
Adaptive Orchestrator v5 - Core Engine Package.

Provides the deterministic, zero-dependency foundation for:
  - Mission & Task domain models
  - Topological Dependency Graph & Safe Dynamic Mutations
  - Logical Readiness Resolver
  - Deterministic Priority Ready Queue
  - MissionEngine Facade & Event System
  - Reusable Worker Pool & Domain Affinity
  - Execution Adapter Abstraction
  - AIMD Adaptive Concurrency Controller & Feedback Signals
  - Intelligent Model Routing
"""

from orchestrator.engine import MissionEngine
from orchestrator.exceptions import (
    AdaptiveOrchestratorError,
    CycleDetectedError,
    DependencyError,
    DuplicateQueueEntryError,
    DuplicateTaskError,
    DuplicateWorkerError,
    GraphMutationError,
    InvalidStateTransitionError,
    InvalidWorkerStateError,
    NoAvailableWorkerError,
    ReadyQueueError,
    SelfDependencyError,
    TaskError,
    TaskNotFoundError,
    TaskNotReadyError,
    UnknownDependencyError,
    WorkerError,
    WorkerNotFoundError,
    WorkspaceAcquisitionError,
    WorkspaceConflictError,
    WorkspaceError,
    WorkspaceNotFoundError,
    IntegrationError,
    MergeConflictError,
    MergeQueueError,
)
from orchestrator.graph.dag import DependencyGraph
from orchestrator.graph.mutations import GraphMutationEngine
from orchestrator.integration import (
    GitMergeAdapter,
    IntegrationManager,
    MergeAdapter,
    MergeQueue,
    MergeRequest,
    MergeResult,
    MergeStatus,
    MockMergeAdapter,
)
from orchestrator.models import (
    Event,
    EventType,
    Mission,
    MissionState,
    Task,
    TaskState,
)
from orchestrator.resolver import DependencyResolver
from orchestrator.routing import (
    ExecutionProfile,
    ModelRouter,
    ModelTier,
    RouterConfig,
)
from orchestrator.persistence import (
    CheckpointPolicy,
    CheckpointTrigger,
    InterruptedTaskRecovery,
    PersistenceManager,
    RecoveryReport,
    SerializedMissionState,
    atomic_write_json,
)
from orchestrator.telemetry import (
    MissionMetrics,
    RoutingMetrics,
    SchedulerMetrics,
    TelemetryCollector,
    TelemetryReport,
    VerificationMetrics,
    WorkerMetricsReport,
    WorkspaceMetrics,
)
from orchestrator.verification import (
    FailureClassification,
    IndependentVerifier,
    MockAdversarialVerifier,
    MockIndependentVerifier,
    MockVictoryAuditVerifier,
    RepairCoordinator,
    RepairPayload,
    Tier1SelfTestVerifier,
    Tier3AdversarialVerifier,
    Tier4VictoryAuditVerifier,
    VerificationEngine,
    VerificationEvidence,
    VerificationPolicy,
    VerificationResult,
    VerificationStatus,
    VerificationTier,
    Verifier,
    VictoryAuditResult,
    classify_failure,
    resolve_policy_for_task,
    truncate_summary,
)
from orchestrator.scheduler.aimd import (
    AIMDConfig,
    AIMDController,
    AIMDState,
)
from orchestrator.scheduler.feedback import (
    FeedbackCollector,
    FeedbackSignal,
    FeedbackSignalType,
)
from orchestrator.scheduler.ready_queue import ReadyQueue
from orchestrator.scheduler.scheduler import EventDrivenScheduler, ScheduledDispatch
from orchestrator.workers.adapter import (
    ExecutionAdapter,
    ExecutionResult,
    LocalExecutionAdapter,
    MockExecutionAdapter,
)
from orchestrator.workers.affinity import DomainAffinityPolicy
from orchestrator.workers.models import (
    Worker,
    WorkerMetrics,
    WorkerState,
)
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workspace import (
    CollisionDetector,
    MockWorktreeAdapter,
    NativeWorktreeAdapter,
    WorkspaceMode,
    WorkspaceRecord,
    WorkspaceRegistry,
    WorkspaceReleaseState,
    WorktreeAdapter,
    are_write_sets_overlapping,
    is_path_overlap,
    normalize_path,
)

__all__ = [
    # Facade
    "MissionEngine",
    # Models
    "Mission",
    "MissionState",
    "Task",
    "TaskState",
    "Event",
    "EventType",
    # Graph & Resolver
    "DependencyGraph",
    "GraphMutationEngine",
    "DependencyResolver",
    # Scheduler & AIMD
    "ReadyQueue",
    "EventDrivenScheduler",
    "ScheduledDispatch",
    "AIMDConfig",
    "AIMDController",
    "AIMDState",
    "FeedbackCollector",
    "FeedbackSignal",
    "FeedbackSignalType",
    # Routing
    "ModelTier",
    "ExecutionProfile",
    "RouterConfig",
    "ModelRouter",
    # Workers
    "Worker",
    "WorkerState",
    "WorkerMetrics",
    "WorkerRegistry",
    "DomainAffinityPolicy",
    "ExecutionAdapter",
    "ExecutionResult",
    "MockExecutionAdapter",
    "LocalExecutionAdapter",
    # Workspace & Collision
    "WorkspaceMode",
    "WorkspaceReleaseState",
    "WorkspaceRecord",
    "CollisionDetector",
    "normalize_path",
    "is_path_overlap",
    "are_write_sets_overlapping",
    "WorkspaceRegistry",
    "WorktreeAdapter",
    "MockWorktreeAdapter",
    "NativeWorktreeAdapter",
    # Integration & Merge
    "MergeStatus",
    "MergeRequest",
    "MergeResult",
    "MergeAdapter",
    "MockMergeAdapter",
    "GitMergeAdapter",
    "MergeQueue",
    "IntegrationManager",
    # Verification & Repair
    "VerificationTier",
    "VerificationStatus",
    "FailureClassification",
    "VerificationEvidence",
    "VerificationResult",
    "VerificationPolicy",
    "resolve_policy_for_task",
    "Verifier",
    "Tier1SelfTestVerifier",
    "IndependentVerifier",
    "MockIndependentVerifier",
    "Tier3AdversarialVerifier",
    "Tier4VictoryAuditVerifier",
    "MockAdversarialVerifier",
    "MockVictoryAuditVerifier",
    "VictoryAuditResult",
    "RepairCoordinator",
    "RepairPayload",
    "classify_failure",
    "VerificationEngine",
    "truncate_summary",
    # Persistence
    "CheckpointTrigger",
    "SerializedMissionState",
    "InterruptedTaskRecovery",
    "RecoveryReport",
    "CheckpointPolicy",
    "PersistenceManager",
    "atomic_write_json",
    # Telemetry
    "MissionMetrics",
    "SchedulerMetrics",
    "WorkerMetricsReport",
    "WorkspaceMetrics",
    "VerificationMetrics",
    "RoutingMetrics",
    "TelemetryReport",
    "TelemetryCollector",
    # Exceptions
    "AdaptiveOrchestratorError",
    "TaskError",
    "DuplicateTaskError",
    "TaskNotFoundError",
    "InvalidStateTransitionError",
    "DependencyError",
    "SelfDependencyError",
    "CycleDetectedError",
    "UnknownDependencyError",
    "GraphMutationError",
    "ReadyQueueError",
    "DuplicateQueueEntryError",
    "TaskNotReadyError",
    "WorkerError",
    "DuplicateWorkerError",
    "WorkerNotFoundError",
    "InvalidWorkerStateError",
    "NoAvailableWorkerError",
    "WorkspaceError",
    "WorkspaceConflictError",
    "WorkspaceAcquisitionError",
    "WorkspaceNotFoundError",
    "IntegrationError",
    "MergeConflictError",
    "MergeQueueError",
]

