"""
Adaptive Orchestrator v5 - Write-Set Collision Detection.
Provides deterministic path normalization and write-set overlap detection
to enforce the Fundamental Law: READ PARALLEL — WRITE CONTROLLED.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from orchestrator.models import Task
    from orchestrator.workspace.models import WorkspaceRecord


class OverlapResult(tuple):
    """2-tuple (has_overlap, pair) that evaluates directly to boolean in conditional statements."""
    def __new__(cls, has_overlap: bool, pair: Optional[Tuple[str, str]] = None):
        return super().__new__(cls, (has_overlap, pair))

    def __bool__(self) -> bool:
        return self[0]


def normalize_path(path: str) -> str:
    """
    Deterministically normalizes a file or directory path:
    - Strips leading/trailing whitespace
    - Replaces backslashes with forward slashes
    - Normalizes case to lowercase for deterministic cross-platform comparison
    - Eliminates redundant slashes and relative segments (. / ..)
    - Strips leading and trailing slashes for canonical representation
    """
    if not path or not path.strip():
        return "."

    cleaned = path.strip().replace("\\", "/").lower()

    # Clean relative components deterministically
    norm = posixpath.normpath(cleaned)
    if norm.startswith("/"):
        norm = norm.lstrip("/")

    norm = norm.rstrip("/")
    return norm or "."


def is_path_overlap(path_a: str, path_b: str) -> bool:
    """
    Determines whether two paths overlap on writable resources:
    - Exact file matches: 'src/a.py' vs 'src/a.py' -> True
    - Parent directory vs child file: 'src/api/' vs 'src/api/routes.py' -> True
    - Parent directory vs child file (no trailing slash): 'src/api' vs 'src/api/routes.py' -> True
    - Sibling paths: 'src/api' vs 'src/api_utils.py' -> False (disjoint)
    - Glob/pattern matching: 'src/api/*.py' vs 'src/api/routes.py' -> True
    """
    norm_a = normalize_path(path_a)
    norm_b = normalize_path(path_b)

    if not norm_a or not norm_b or norm_a == "." or norm_b == ".":
        # Root overlaps with everything writable
        if norm_a == "." or norm_b == ".":
            return True
        return False

    # 1. Exact match
    if norm_a == norm_b:
        return True

    # 2. Pattern / Glob matching
    if any(c in norm_a for c in ("*", "?", "[")):
        if fnmatch.fnmatch(norm_b, norm_a):
            return True
    if any(c in norm_b for c in ("*", "?", "[")):
        if fnmatch.fnmatch(norm_a, norm_b):
            return True

    # 3. Path component hierarchy matching
    clean_a = norm_a.rstrip("/*").rstrip("/")
    clean_b = norm_b.rstrip("/*").rstrip("/")

    parts_a = [p for p in clean_a.split("/") if p]
    parts_b = [p for p in clean_b.split("/") if p]

    if not parts_a or not parts_b:
        return False

    if parts_a == parts_b:
        return True

    # Check if one is an exact prefix directory of the other
    min_len = min(len(parts_a), len(parts_b))
    if parts_a[:min_len] == parts_b[:min_len]:
        # Prefix matches at component boundary!
        return True

    return False


def are_write_sets_overlapping(
    set_a: Iterable[str],
    set_b: Iterable[str]
) -> OverlapResult:
    """
    Compares two write-sets for any overlapping resources.
    Returns OverlapResult (evaluates as bool) containing (has_overlap, Optional[pair]).
    """
    norm_set_a = [normalize_path(p) for p in set_a if p]
    norm_set_b = [normalize_path(p) for p in set_b if p]

    for pa in norm_set_a:
        for pb in norm_set_b:
            if is_path_overlap(pa, pb):
                return OverlapResult(True, (pa, pb))

    return OverlapResult(False, None)



class CollisionDetector:
    """
    Evaluates concurrency safety between tasks and active workspace ownerships.
    """

    @staticmethod
    def can_execute_concurrently(task_a: Task, task_b: Task) -> Tuple[bool, str]:
        """
        Answers: Can task A and task B safely execute concurrently?
        Based purely on their declared write_sets and workspace modes.
        """
        # If neither writes, read parallel is always safe
        if not task_a.write_set and not task_b.write_set:
            return True, "Both tasks are read-only"

        # If only one writes:
        # In isolated branch worktrees, readers and writers are disjointly isolated.
        # But if the writer is in shared/inherit mode, reading concurrent to that write is unsafe.
        if not task_a.write_set or not task_b.write_set:
            writer = task_a if task_a.write_set else task_b
            reader = task_b if task_a.write_set else task_a
            if writer.workspace_mode in ("inherit", "share"):
                # Check if reader reads what writer writes
                overlap, pair = are_write_sets_overlapping(writer.write_set, reader.read_set)
                if overlap and pair:
                    return False, f"Reader '{reader.task_id}' conflicts with shared writer '{writer.task_id}' on '{pair[1]}'"
            return True, "Disjoint reader and writer"

        # Both write: check for write collisions
        overlap, pair = are_write_sets_overlapping(task_a.write_set, task_b.write_set)
        if overlap and pair:
            return False, f"Write set collision between '{task_a.task_id}' and '{task_b.task_id}' on '{pair[0]}' and '{pair[1]}'"

        return True, ""

    @staticmethod
    def has_write_conflict(
        candidate_task: Task,
        active_tasks: Iterable[Task]
    ) -> Tuple[bool, str, str]:
        """
        Checks if candidate_task has a write conflict with any task in active_tasks.
        Returns (has_conflict, conflicting_task_id, reason).
        """
        if not candidate_task.write_set:
            return False, "", ""

        for active in active_tasks:
            if active.task_id == candidate_task.task_id:
                continue
            if not getattr(active, "write_set", None):
                continue

            overlap, pair = are_write_sets_overlapping(candidate_task.write_set, active.write_set)
            if overlap and pair:
                reason = f"Write set collision between '{candidate_task.task_id}' and '{active.task_id}' on '{pair[0]}' and '{pair[1]}'"
                return True, active.task_id, reason

        return False, "", ""

    @staticmethod
    def detect_active_conflict(
        task: Task,
        active_records: Iterable[WorkspaceRecord]
    ) -> Optional[Tuple[WorkspaceRecord, str, str]]:
        """
        Checks whether candidate task conflicts with any currently active workspace ownership.
        Returns (conflicting_record, task_path, active_path) or None.
        """
        if not task.write_set:
            # Read-only task does not conflict with isolated branch writers
            return None

        for record in active_records:
            if not record.is_active:
                continue
            if record.task_id == task.task_id:
                continue

            if not record.write_set:
                continue

            overlap, pair = are_write_sets_overlapping(task.write_set, record.write_set)
            if overlap and pair:
                return record, pair[0], pair[1]

        return None

