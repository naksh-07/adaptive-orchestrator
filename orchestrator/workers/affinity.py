"""
Adaptive Orchestrator v5 - Domain Affinity Policy.
"""

from __future__ import annotations

from typing import List, Optional

from orchestrator.models import Task
from orchestrator.workers.models import Worker


class DomainAffinityPolicy:
    """
    Evaluates and selects the optimal available worker for a task based on domain affinity.
    Enforces deterministic selection:
      1. Exact domain match (task.domain == worker.domain)
      2. General domain fallback (worker.domain == "general") if permitted
      3. Any idle worker if cross-domain fallback is explicitly enabled
      4. Deterministic tie-breaking:
         - Fewest tasks completed (load balancing)
         - Oldest last_active_at (longest idle time)
         - Alphabetical worker_id
    """

    def __init__(
        self,
        allow_general_fallback: bool = True,
        allow_cross_domain_fallback: bool = False,
        general_domain_name: str = "general",
    ) -> None:
        self.allow_general_fallback = allow_general_fallback
        self.allow_cross_domain_fallback = allow_cross_domain_fallback
        self.general_domain_name = general_domain_name

    def rank_candidates(self, task: Task, idle_workers: List[Worker]) -> List[Worker]:
        """
        Filters and ranks available idle workers for the specified task.
        Returns a sorted list of candidate workers in priority order.
        """
        if not idle_workers:
            return []

        # 1. Exact domain matches
        exact_matches = [w for w in idle_workers if w.domain == task.domain]
        if exact_matches:
            return self._sort_candidates(exact_matches)

        # 2. Fallback to general domain workers
        if self.allow_general_fallback:
            general_matches = [w for w in idle_workers if w.domain == self.general_domain_name]
            if general_matches:
                return self._sort_candidates(general_matches)

        # 3. Fallback to any idle worker if cross-domain allowed
        if self.allow_cross_domain_fallback:
            return self._sort_candidates(list(idle_workers))

        return []

    def select_worker(self, task: Task, idle_workers: List[Worker]) -> Optional[Worker]:
        """
        Selects the single best candidate worker for the given task, or None if no suitable worker is available.
        """
        ranked = self.rank_candidates(task, idle_workers)
        return ranked[0] if ranked else None

    def _sort_candidates(self, candidates: List[Worker]) -> List[Worker]:
        """
        Sorts candidates deterministically by:
        1. Fewest tasks completed
        2. Oldest last_active_at (longest idle)
        3. worker_id (alphabetical)
        """
        return sorted(
            candidates,
            key=lambda w: (
                w.metrics.tasks_completed,
                w.last_active_at,
                w.worker_id,
            )
        )
