"""
Adaptive Orchestrator v5 - Dependency Resolver & Readiness Calculation.
"""

from __future__ import annotations

from typing import List, Set

from orchestrator.graph.dag import DependencyGraph
from orchestrator.models import Task, TaskState


class DependencyResolver:
    """
    Evaluates and calculates logical task readiness in the DependencyGraph.
    Strictly answers 'WHAT CAN RUN?' based purely on prerequisite state,
    without worker, concurrency, or environmental constraints.
    """

    def __init__(self, graph: DependencyGraph) -> None:
        self._graph = graph

    def is_task_ready(self, task_id: str) -> bool:
        """
        Determines if a task is logically ready to execute.
        A task is ready if:
        1. It is currently in PENDING, BLOCKED, or already READY state.
        2. All of its direct dependencies have reached PASSED state.
        3. It is not RUNNING, VERIFYING, PASSED, FAILED, or CANCELLED.
        """
        task = self._graph.get_task(task_id)

        # Terminal, active, or in-flight tasks cannot become newly ready
        if task.status in (
            TaskState.RUNNING,
            TaskState.VERIFYING,
            TaskState.PASSED,
            TaskState.FAILED,
            TaskState.RETRYING,
            TaskState.CANCELLED,
        ):
            return False

        # If it has no dependencies, it is ready
        if not task.dependencies:
            return True

        # Check all direct dependencies
        for dep_id in task.dependencies:
            dep_task = self._graph.get_task(dep_id)
            if not dep_task.status.is_success:
                return False

        return True

    def calculate_unlock_value(self, task_id: str) -> int:
        """
        Calculates the dependency unlock value for task_id:
        The number of direct dependents that would have ALL their prerequisites
        satisfied if task_id were to transition to PASSED.
        Used as a priority heuristic for the Ready Queue.
        """
        task = self._graph.get_task(task_id)
        unlock_count = 0

        for dep_id in task.dependents:
            dependent = self._graph.get_task(dep_id)
            if dependent.status.is_terminal or dependent.status == TaskState.RUNNING:
                continue

            # Check if task_id is the ONLY unsatisfied prerequisite for dependent
            unsatisfied = [
                p_id for p_id in dependent.dependencies
                if p_id != task_id and not self._graph.get_task(p_id).status.is_success
            ]
            if len(unsatisfied) == 0:
                unlock_count += 1

        return unlock_count

    def resolve_dependents_on_completion(self, completed_task_id: str) -> List[Task]:
        """
        Evaluates all direct dependents of a task that just transitioned to PASSED.
        Any dependent whose prerequisites are now fully satisfied transitions from PENDING/BLOCKED to READY.
        Returns the list of newly ready Task objects in deterministic order.
        """
        completed_task = self._graph.get_task(completed_task_id)
        newly_ready: List[Task] = []

        for dep_id in sorted(completed_task.dependents):
            dependent = self._graph.get_task(dep_id)
            if dependent.status in (TaskState.PENDING, TaskState.BLOCKED):
                if self.is_task_ready(dep_id):
                    dependent.transition_to(TaskState.READY, reason=f"Dependency '{completed_task_id}' satisfied")
                    newly_ready.append(dependent)

        return newly_ready

    def resolve_dependents_on_failure(self, failed_task_id: str) -> List[Task]:
        """
        Evaluates all direct dependents of a task that failed or was cancelled.
        Any dependent currently in PENDING transitions to BLOCKED.
        Returns the list of newly blocked Task objects in deterministic order.
        """
        failed_task = self._graph.get_task(failed_task_id)
        newly_blocked: List[Task] = []

        for dep_id in sorted(failed_task.dependents):
            dependent = self._graph.get_task(dep_id)
            if dependent.status == TaskState.PENDING:
                dependent.transition_to(TaskState.BLOCKED, reason=f"Dependency '{failed_task_id}' failed/cancelled")
                newly_blocked.append(dependent)

        return newly_blocked

    def resolve_all_ready(self) -> List[Task]:
        """
        Scans all tasks in the graph. Any PENDING or BLOCKED task whose prerequisites
        are satisfied transitions to READY.
        Returns all tasks currently in READY state, sorted deterministically by task_id.
        """
        ready_tasks: List[Task] = []

        for task in sorted(self._graph.all_tasks(), key=lambda t: t.task_id):
            if task.status in (TaskState.PENDING, TaskState.BLOCKED):
                if self.is_task_ready(task.task_id):
                    task.transition_to(TaskState.READY, reason="All dependencies satisfied")
                    ready_tasks.append(task)
            elif task.status == TaskState.READY:
                ready_tasks.append(task)

        return ready_tasks
