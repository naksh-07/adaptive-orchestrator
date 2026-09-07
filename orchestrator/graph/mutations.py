"""
Adaptive Orchestrator v5 - Safe Dynamic Graph Mutation Protocols.
"""

from __future__ import annotations

from typing import List, Optional

from orchestrator.exceptions import GraphMutationError, TaskNotFoundError
from orchestrator.graph.dag import DependencyGraph
from orchestrator.models import Task, TaskState


class GraphMutationEngine:
    """
    Executes controlled dynamic mutations on a DependencyGraph while enforcing
    strict acyclicity and state invariants.
    """

    def __init__(self, graph: DependencyGraph) -> None:
        self._graph = graph

    def add_task(self, task: Task) -> None:
        """Dynamically adds a new task to the graph, preserving DAG invariants."""
        self._graph.add_task(task)

    def add_dependency(self, dependent_id: str, prerequisite_id: str) -> None:
        """
        Dynamically adds a dependency (prerequisite -> dependent).
        Rejects cycles, self-dependencies, or missing tasks immediately.
        """
        self._graph.add_dependency(dependent_id, prerequisite_id)

    def remove_dependency(self, dependent_id: str, prerequisite_id: str) -> bool:
        """
        Safely removes a dependency edge.
        Returns True if the dependency was removed, False if it was not present.
        """
        return self._graph.remove_dependency(dependent_id, prerequisite_id)

    def invalidate_task(self, task_id: str, reason: str = "") -> List[str]:
        """
        Invalidates task_id and all of its transitive descendants.
        Tasks currently in PASSED, READY, RUNNING, or VERIFYING transition back to PENDING.
        Returns the list of invalidated task IDs in deterministic topological order.
        """
        if not self._graph.has_task(task_id):
            raise TaskNotFoundError(f"Cannot invalidate non-existent task '{task_id}'.")

        # Gather target and all descendants
        descendants = self._graph.get_descendants(task_id)
        affected_ids = {task_id}.union(descendants)

        # Sort affected IDs topologically for deterministic invalidation
        full_order = self._graph.topological_sort()
        ordered_affected = [tid for tid in full_order if tid in affected_ids]

        # Invalidate in topological order
        for tid in ordered_affected:
            task = self._graph.get_task(tid)
            if task.status != TaskState.CANCELLED:
                # Reset task state to PENDING
                if task.status in (TaskState.PASSED, TaskState.FAILED):
                    # Force transition back to PENDING
                    task.status = TaskState.PENDING
                elif task.status in (TaskState.READY, TaskState.RUNNING, TaskState.VERIFYING, TaskState.BLOCKED):
                    task.status = TaskState.PENDING
                task.result = None
                task.error = None
                task.completed_at = None

        return ordered_affected
