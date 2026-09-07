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
)
from orchestrator.graph.dag import DependencyGraph
from orchestrator.graph.mutations import GraphMutationEngine
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
]
