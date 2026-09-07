"""
Adaptive Orchestrator v5 - Dependency Directed Acyclic Graph (DAG).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from orchestrator.exceptions import (
    CycleDetectedError,
    DuplicateTaskError,
    SelfDependencyError,
    TaskNotFoundError,
    UnknownDependencyError,
)
from orchestrator.models import Task


class DependencyGraph:
    """
    Core Directed Acyclic Graph (DAG) for managing mission task dependencies.
    Provides strict cycle detection, topological ordering, and dependent tracking.
    """

    def __init__(self) -> None:
        self._tasks: Dict[str, Task] = {}

    def add_task(self, task: Task) -> None:
        """
        Adds a task to the graph.
        Raises DuplicateTaskError if a task with the same task_id already exists.
        """
        if task.task_id in self._tasks:
            raise DuplicateTaskError(f"Task with ID '{task.task_id}' already exists in the graph.")

        # Ensure task dependencies point to existing tasks if any pre-populated
        for dep_id in task.dependencies:
            if dep_id not in self._tasks:
                raise UnknownDependencyError(
                    f"Task '{task.task_id}' specifies unknown dependency '{dep_id}'."
                )

        self._tasks[task.task_id] = task

        # Synchronize reciprocal dependent edges for pre-populated dependencies
        for dep_id in task.dependencies:
            self._tasks[dep_id].dependents.add(task.task_id)

        # Validate that adding pre-populated dependencies didn't introduce a cycle
        cycle = self.detect_cycle()
        if cycle:
            # Rollback addition
            for dep_id in task.dependencies:
                self._tasks[dep_id].dependents.discard(task.task_id)
            del self._tasks[task.task_id]
            raise CycleDetectedError(f"Adding task '{task.task_id}' introduced a cycle: {' -> '.join(cycle)}")

    def get_task(self, task_id: str) -> Task:
        """
        Retrieves a task by task_id.
        Raises TaskNotFoundError if the task does not exist.
        """
        if task_id not in self._tasks:
            raise TaskNotFoundError(f"Task with ID '{task_id}' not found in the graph.")
        return self._tasks[task_id]

    def has_task(self, task_id: str) -> bool:
        """Returns True if the task exists in the graph."""
        return task_id in self._tasks

    def all_tasks(self) -> List[Task]:
        """Returns a list of all tasks currently in the graph."""
        return list(self._tasks.values())

    def task_count(self) -> int:
        """Returns total number of tasks in the graph."""
        return len(self._tasks)

    def add_dependency(self, dependent_id: str, prerequisite_id: str) -> None:
        """
        Declares that task dependent_id depends on prerequisite_id (prerequisite -> dependent).
        Raises:
            SelfDependencyError: If dependent_id == prerequisite_id.
            TaskNotFoundError: If dependent_id does not exist.
            UnknownDependencyError: If prerequisite_id does not exist.
            CycleDetectedError: If adding the dependency would create a cycle.
        """
        if dependent_id == prerequisite_id:
            raise SelfDependencyError(f"Task '{dependent_id}' cannot depend on itself.")

        if dependent_id not in self._tasks:
            raise TaskNotFoundError(f"Dependent task '{dependent_id}' not found in graph.")

        if prerequisite_id not in self._tasks:
            raise UnknownDependencyError(f"Prerequisite task '{prerequisite_id}' not found in graph.")

        dependent_task = self._tasks[dependent_id]
        prerequisite_task = self._tasks[prerequisite_id]

        # If edge already exists, idempotent success
        if prerequisite_id in dependent_task.dependencies:
            return

        # Pre-check: if prerequisite is already a descendant of dependent, adding this edge creates a cycle
        descendants = self.get_descendants(dependent_id)
        if prerequisite_id in descendants:
            raise CycleDetectedError(
                f"Adding dependency '{dependent_id} -> {prerequisite_id}' creates a cycle "
                f"because '{prerequisite_id}' is already a descendant of '{dependent_id}'."
            )

        # Apply edge
        dependent_task.dependencies.add(prerequisite_id)
        prerequisite_task.dependents.add(dependent_id)

        # Double check overall graph integrity
        cycle = self.detect_cycle()
        if cycle:
            # Rollback
            dependent_task.dependencies.remove(prerequisite_id)
            prerequisite_task.dependents.remove(dependent_id)
            raise CycleDetectedError(
                f"Adding dependency '{prerequisite_id} -> {dependent_id}' introduced a cycle: {' -> '.join(cycle)}"
            )

    def remove_dependency(self, dependent_id: str, prerequisite_id: str) -> bool:
        """
        Removes a dependency between dependent_id and prerequisite_id.
        Returns True if the edge existed and was removed, False if it did not exist.
        Raises:
            TaskNotFoundError: If dependent_id does not exist.
            UnknownDependencyError: If prerequisite_id does not exist.
        """
        if dependent_id not in self._tasks:
            raise TaskNotFoundError(f"Dependent task '{dependent_id}' not found in graph.")

        if prerequisite_id not in self._tasks:
            raise UnknownDependencyError(f"Prerequisite task '{prerequisite_id}' not found in graph.")

        dependent_task = self._tasks[dependent_id]
        prerequisite_task = self._tasks[prerequisite_id]

        if prerequisite_id in dependent_task.dependencies:
            dependent_task.dependencies.remove(prerequisite_id)
            prerequisite_task.dependents.discard(dependent_id)
            return True
        return False

    def get_dependencies(self, task_id: str) -> Set[str]:
        """Returns direct prerequisites for task_id."""
        return set(self.get_task(task_id).dependencies)

    def get_dependents(self, task_id: str) -> Set[str]:
        """Returns direct dependents for task_id."""
        return set(self.get_task(task_id).dependents)

    def get_ancestors(self, task_id: str) -> Set[str]:
        """
        Returns all transitive prerequisites for task_id (nodes that must execute before task_id).
        """
        self.get_task(task_id)  # Validate existence
        ancestors: Set[str] = set()
        stack = list(self._tasks[task_id].dependencies)

        while stack:
            curr = stack.pop()
            if curr not in ancestors:
                ancestors.add(curr)
                if curr in self._tasks:
                    stack.extend(self._tasks[curr].dependencies)

        return ancestors

    def get_descendants(self, task_id: str) -> Set[str]:
        """
        Returns all transitive dependents for task_id (nodes that depend on task_id).
        """
        self.get_task(task_id)  # Validate existence
        descendants: Set[str] = set()
        stack = list(self._tasks[task_id].dependents)

        while stack:
            curr = stack.pop()
            if curr not in descendants:
                descendants.add(curr)
                if curr in self._tasks:
                    stack.extend(self._tasks[curr].dependents)

        return descendants

    def get_root_tasks(self) -> List[Task]:
        """Returns all tasks that have zero dependencies (in-degree == 0)."""
        return [task for task in self._tasks.values() if len(task.dependencies) == 0]

    def get_leaf_tasks(self) -> List[Task]:
        """Returns all tasks that have zero dependents (out-degree == 0)."""
        return [task for task in self._tasks.values() if len(task.dependents) == 0]

    def detect_cycle(self) -> Optional[List[str]]:
        """
        Detects if the graph contains any cycle using DFS 3-coloring.
        Returns the cycle path as a list of task IDs if found, or None if the graph is acyclic.
        """
        # 0: UNVISITED, 1: VISITING, 2: VISITED
        visited: Dict[str, int] = {task_id: 0 for task_id in self._tasks}
        parent: Dict[str, Optional[str]] = {task_id: None for task_id in self._tasks}

        for start_node in sorted(self._tasks.keys()):
            if visited[start_node] != 0:
                continue

            cycle_path = self._dfs_cycle(start_node, visited, parent)
            if cycle_path is not None:
                return cycle_path

        return None

    def _dfs_cycle(
        self,
        node: str,
        visited: Dict[str, int],
        parent: Dict[str, Optional[str]]
    ) -> Optional[List[str]]:
        visited[node] = 1  # Visiting

        # Traverse downstream edges: node -> dependent
        for neighbor in sorted(self._tasks[node].dependents):
            if neighbor not in visited:
                continue

            if visited[neighbor] == 1:
                # Cycle detected! Reconstruct path
                cycle = [neighbor, node]
                curr = node
                while parent[curr] is not None and parent[curr] != neighbor:
                    curr = parent[curr]
                    cycle.append(curr)
                cycle.append(neighbor)
                cycle.reverse()
                return cycle

            if visited[neighbor] == 0:
                parent[neighbor] = node
                result = self._dfs_cycle(neighbor, visited, parent)
                if result is not None:
                    return result

        visited[node] = 2  # Visited
        return None

    def topological_sort(self) -> List[str]:
        """
        Computes a deterministic topological ordering of all tasks using Kahn's algorithm.
        Raises CycleDetectedError if the graph contains a cycle.
        """
        in_degree: Dict[str, int] = {
            task_id: len(task.dependencies) for task_id, task in self._tasks.items()
        }

        # Deterministic sorting for tie-breaking
        ready: List[str] = sorted([
            task_id for task_id, degree in in_degree.items() if degree == 0
        ])

        order: List[str] = []

        while ready:
            curr_id = ready.pop(0)
            order.append(curr_id)

            # For each dependent, decrement in-degree
            newly_ready = []
            for dependent_id in sorted(self._tasks[curr_id].dependents):
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    newly_ready.append(dependent_id)

            # Insert newly ready nodes and re-sort to maintain determinism
            ready.extend(newly_ready)
            ready.sort()

        if len(order) != len(self._tasks):
            cycle = self.detect_cycle()
            cycle_desc = f" ({' -> '.join(cycle)})" if cycle else ""
            raise CycleDetectedError(f"Graph contains a cycle and cannot be topologically sorted{cycle_desc}.")

        return order

    def validate_integrity(self) -> None:
        """
        Validates overall graph consistency:
        1. All dependencies point to existing tasks.
        2. Reciprocal links (A in B.dependencies <=> B in A.dependents) match exactly.
        3. The graph is strictly acyclic.
        """
        for task_id, task in self._tasks.items():
            for dep_id in task.dependencies:
                if dep_id not in self._tasks:
                    raise UnknownDependencyError(
                        f"Task '{task_id}' references non-existent dependency '{dep_id}'."
                    )
                if task_id not in self._tasks[dep_id].dependents:
                    raise DependencyError(
                        f"Inconsistent graph: '{task_id}' depends on '{dep_id}', "
                        f"but '{dep_id}' does not list '{task_id}' as dependent."
                    )

            for dep_id in task.dependents:
                if dep_id not in self._tasks:
                    raise UnknownDependencyError(
                        f"Task '{task_id}' references non-existent dependent '{dep_id}'."
                    )
                if task_id not in self._tasks[dep_id].dependencies:
                    raise DependencyError(
                        f"Inconsistent graph: '{dep_id}' is dependent of '{task_id}', "
                        f"but '{dep_id}' does not list '{task_id}' as dependency."
                    )

        cycle = self.detect_cycle()
        if cycle:
            raise CycleDetectedError(f"Graph integrity failure: cycle detected: {' -> '.join(cycle)}")
