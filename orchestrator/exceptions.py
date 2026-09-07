"""
Adaptive Orchestrator v5 - Core Engine Exceptions.
"""


class AdaptiveOrchestratorError(Exception):
    """Base exception for all Adaptive Orchestrator errors."""


class TaskError(AdaptiveOrchestratorError):
    """Base exception for task-related errors."""


class DuplicateTaskError(TaskError):
    """Raised when attempting to add a task with an existing task_id."""


class TaskNotFoundError(TaskError):
    """Raised when a requested task does not exist in the graph."""


class InvalidStateTransitionError(TaskError):
    """Raised when attempting an invalid task or mission state transition."""


class DependencyError(AdaptiveOrchestratorError):
    """Base exception for dependency-related errors."""


class SelfDependencyError(DependencyError):
    """Raised when a task attempts to depend on itself."""


class CycleDetectedError(DependencyError):
    """Raised when a dependency addition or mutation would introduce a cycle."""


class UnknownDependencyError(DependencyError):
    """Raised when a task declares a dependency on a nonexistent task."""


class GraphMutationError(AdaptiveOrchestratorError):
    """Raised when a dynamic graph mutation fails integrity checks."""


class ReadyQueueError(AdaptiveOrchestratorError):
    """Base exception for ready queue errors."""


class DuplicateQueueEntryError(ReadyQueueError):
    """Raised when attempting to push a task that is already in the ready queue."""


class TaskNotReadyError(ReadyQueueError):
    """Raised when attempting to enqueue a task that is not in READY state."""
