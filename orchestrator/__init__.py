"""
Adaptive Orchestrator v5 - Core Engine Package.

Provides the deterministic, zero-dependency foundation for:
  - Mission & Task domain models
  - Topological Dependency Graph & Safe Dynamic Mutations
  - Logical Readiness Resolver
  - Deterministic Priority Ready Queue
  - MissionEngine Facade & Event System
"""

from orchestrator.engine import MissionEngine
from orchestrator.exceptions import (
    AdaptiveOrchestratorError,
    CycleDetectedError,
    DependencyError,
    DuplicateQueueEntryError,
    DuplicateTaskError,
    GraphMutationError,
    InvalidStateTransitionError,
    ReadyQueueError,
    SelfDependencyError,
    TaskError,
    TaskNotFoundError,
    TaskNotReadyError,
    UnknownDependencyError,
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
from orchestrator.scheduler.ready_queue import ReadyQueue

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
    # Scheduler
    "ReadyQueue",
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
]
