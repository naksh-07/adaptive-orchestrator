"""
Adaptive Orchestrator v5 - Deterministic Priority Ready Queue.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

from orchestrator.exceptions import DuplicateQueueEntryError, TaskNotReadyError
from orchestrator.models import Task, TaskState


@dataclass(order=True)
class _QueueItem:
    """
    Internal wrapper for priority queue ordering.
    Ordering tuple: (-priority, -unlock_value, entry_seq, task_id)
    Higher priority and unlock value come first; lower sequence (FIFO) comes first.
    """
    priority_neg: float
    unlock_value_neg: int
    entry_seq: int
    task_id: str
    task: Task = field(compare=False)


class ReadyQueue:
    """
    Deterministic Priority Ready Queue for logically READY tasks.
    Enforces that only tasks in TaskState.READY may enter.
    Prioritizes tasks by:
      1. Explicit task priority (descending)
      2. Dependency unlock value (descending)
      3. Entry sequence / age (ascending / FIFO)
      4. Task ID (alphabetical tie-breaker)
    """

    def __init__(self) -> None:
        self._heap: List[_QueueItem] = []
        self._entry_finder: Dict[str, _QueueItem] = {}
        self._sequence_counter: int = 0

    def push(self, task: Task, unlock_value: int = 0) -> None:
        """
        Inserts a task into the ready queue.
        Raises:
            TaskNotReadyError: If task.status != TaskState.READY.
            DuplicateQueueEntryError: If task.task_id is already in the queue.
        """
        if task.status != TaskState.READY:
            raise TaskNotReadyError(
                f"Cannot enqueue task '{task.task_id}': status is {task.status.value}, expected READY."
            )

        if task.task_id in self._entry_finder:
            raise DuplicateQueueEntryError(
                f"Task '{task.task_id}' is already present in the Ready Queue."
            )

        self._sequence_counter += 1
        item = _QueueItem(
            priority_neg=-float(task.priority),
            unlock_value_neg=-int(unlock_value),
            entry_seq=self._sequence_counter,
            task_id=task.task_id,
            task=task,
        )

        heapq.heappush(self._heap, item)
        self._entry_finder[task.task_id] = item

    def pop(self) -> Task:
        """
        Pops and returns the highest priority ready task.
        Raises IndexError if the queue is empty.
        """
        while self._heap:
            item = heapq.heappop(self._heap)
            if item.task_id in self._entry_finder and self._entry_finder[item.task_id] is item:
                del self._entry_finder[item.task_id]
                return item.task

        raise IndexError("pop from empty ReadyQueue")

    def pop_optional(self) -> Optional[Task]:
        """
        Pops and returns the highest priority ready task, or None if empty.
        """
        try:
            return self.pop()
        except IndexError:
            return None

    def peek(self) -> Optional[Task]:
        """
        Returns the highest priority ready task without removing it, or None if empty.
        """
        while self._heap:
            item = self._heap[0]
            if item.task_id in self._entry_finder and self._entry_finder[item.task_id] is item:
                return item.task
            # Stale entry
            heapq.heappop(self._heap)

        return None

    def remove(self, task_id: str) -> Optional[Task]:
        """
        Removes a task by task_id from the queue if present.
        Returns the removed Task, or None if not found.
        """
        item = self._entry_finder.pop(task_id, None)
        if item is not None:
            # We mark it deleted from entry_finder; heap cleanup happens lazily
            return item.task
        return None

    def contains(self, task_id: str) -> bool:
        """Returns True if task_id is currently in the queue."""
        return task_id in self._entry_finder

    def __contains__(self, task_id: str) -> bool:
        return self.contains(task_id)

    def __len__(self) -> int:
        return len(self._entry_finder)

    def is_empty(self) -> bool:
        """Returns True if the ready queue has no items."""
        return len(self._entry_finder) == 0

    def clear(self) -> None:
        """Empties the ready queue."""
        self._heap.clear()
        self._entry_finder.clear()
        self._sequence_counter = 0

    def all_tasks(self) -> List[Task]:
        """
        Returns a snapshot list of all queued tasks in deterministic priority order.
        Does not mutate the queue.
        """
        active_items = [
            item for item in self._heap
            if item.task_id in self._entry_finder and self._entry_finder[item.task_id] is item
        ]
        sorted_items = sorted(active_items)
        return [item.task for item in sorted_items]

    def all_task_ids(self) -> List[str]:
        """Returns a snapshot list of all queued task IDs in deterministic priority order."""
        return [t.task_id for t in self.all_tasks()]
