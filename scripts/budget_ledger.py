#!/usr/bin/env python3
"""
Adaptive Orchestrator v5 — Adaptive Resource & Concurrency Ledger
Tracks dynamic physical worker concurrency, AIMD policy limits,
and worker reuse metrics with decoupled logical DAG width.
"""

import sys
import json
import argparse
from typing import Dict, List, Optional, Any, Set

DEFAULT_MIN_CAPACITY: int = 1
DEFAULT_MAX_CAPACITY: int = 8
DEFAULT_INITIAL_CAPACITY: int = 2

class BudgetLedger:
    """
    v5 Adaptive Resource & Concurrency Ledger.
    Distinguishes clearly between:
      - Logical DAG Width (unbounded task graph)
      - Physical Active Workers (active agent processes)
      - Dynamic Scheduler Capacity (AIMD controller limits)
      - Worker Reuses (context-preserving execution)
    """

    def __init__(
        self,
        max_concurrent: Optional[int] = None,
        max_budget: Optional[int] = None,
        min_capacity: int = DEFAULT_MIN_CAPACITY,
        max_capacity: int = DEFAULT_MAX_CAPACITY,
        initial_capacity: int = DEFAULT_INITIAL_CAPACITY,
    ):
        self.min_capacity = min_capacity
        self.max_capacity = max_concurrent if max_concurrent is not None else max_capacity
        self.current_capacity = min(self.max_capacity, max(self.min_capacity, initial_capacity))
        self.max_budget = max_budget  # None means unbounded (v5 architectural model)

        self.spawned_total: int = 0
        self.tasks_dispatched: int = 0
        self.worker_reuses: int = 0
        self.logical_tasks_count: int = 0

        self.active_workers: Dict[str, Dict[str, Any]] = {}
        self.idle_workers: Dict[str, Dict[str, Any]] = {}
        self.coordinator_quotas: Dict[str, int] = {}
        self.history: List[Dict[str, Any]] = []

    @property
    def max_concurrent(self) -> int:
        return self.max_capacity

    @max_concurrent.setter
    def max_concurrent(self, value: int) -> None:
        self.max_capacity = value

    @property
    def active_total(self) -> int:
        """Calculates active physical workers + active coordinator allocations."""
        direct_active = len(self.active_workers)
        reserved_coords = sum(self.coordinator_quotas.values())
        return direct_active + reserved_coords

    @property
    def remaining_budget(self) -> int:
        if self.max_budget is None:
            return 999999  # Unbounded in v5
        return max(0, self.max_budget - self.spawned_total)

    def can_spawn(self, count: int = 1) -> bool:
        """Checks physical concurrency bounds and optional external budget limits."""
        if self.max_budget is not None and (self.spawned_total + count > self.max_budget):
            return False
        if self.active_total + count > self.max_capacity:
            return False
        return True

    def spawn(
        self,
        worker_id: str,
        role: str,
        domain: str = "general",
        coordinator_id: Optional[str] = None
    ) -> bool:
        """Spawns a new physical worker if within capacity bounds."""
        if not self.can_spawn(1):
            return False
        self.spawned_total += 1
        record = {
            "id": worker_id,
            "role": role,
            "domain": domain,
            "coordinator_id": coordinator_id,
            "status": "ACTIVE",
            "reuses": 0,
        }
        self.active_workers[worker_id] = record
        self.history.append(record)
        return True

    def release_to_idle(self, worker_id: str) -> bool:
        """Transitions an active worker to the idle pool for reuse."""
        if worker_id in self.active_workers:
            w_info = self.active_workers.pop(worker_id)
            w_info["status"] = "IDLE"
            self.idle_workers[worker_id] = w_info
            return True
        return False

    def record_reuse(self, worker_id: str, task_id: str = "") -> bool:
        """Reuses an idle worker without spawning a new physical agent."""
        if worker_id in self.idle_workers:
            w_info = self.idle_workers.pop(worker_id)
            w_info["status"] = "ACTIVE"
            w_info["reuses"] = w_info.get("reuses", 0) + 1
            self.active_workers[worker_id] = w_info
            self.worker_reuses += 1
            self.tasks_dispatched += 1
            return True
        return False

    def terminate(self, worker_id: str) -> bool:
        """Terminates an active or idle worker."""
        if worker_id in self.active_workers:
            self.active_workers[worker_id]["status"] = "TERMINATED"
            del self.active_workers[worker_id]
            return True
        if worker_id in self.idle_workers:
            self.idle_workers[worker_id]["status"] = "TERMINATED"
            del self.idle_workers[worker_id]
            return True
        return False

    def reserve_coordinator_quota(self, coordinator_id: str, quota: int) -> bool:
        if not self.can_spawn(quota):
            return False
        self.coordinator_quotas[coordinator_id] = self.coordinator_quotas.get(coordinator_id, 0) + quota
        return True

    def release_coordinator_quota(self, coordinator_id: str) -> None:
        if coordinator_id in self.coordinator_quotas:
            del self.coordinator_quotas[coordinator_id]

    def collapse_all(self) -> int:
        terminated_count = len(self.active_workers) + len(self.idle_workers)
        self.active_workers.clear()
        self.idle_workers.clear()
        self.coordinator_quotas.clear()
        return terminated_count

    def adjust_capacity(self, delta: int) -> int:
        """Adjusts AIMD capacity within min/max capacity bounds."""
        new_cap = max(self.min_capacity, min(self.max_capacity, self.current_capacity + delta))
        self.current_capacity = new_cap
        return self.current_capacity

    def status_summary(self) -> Dict[str, Any]:
        return {
            "spawned_total": self.spawned_total,
            "active_total": self.active_total,
            "idle_workers_count": len(self.idle_workers),
            "worker_reuses": self.worker_reuses,
            "tasks_dispatched": self.tasks_dispatched,
            "logical_tasks_count": self.logical_tasks_count,
            "current_capacity": self.current_capacity,
            "min_capacity": self.min_capacity,
            "max_capacity": self.max_capacity,
            "max_budget": self.max_budget,
            "remaining_budget": self.remaining_budget,
            "can_spawn_more": self.can_spawn(1),
        }

    def print_ledger(self) -> None:
        print("=" * 65)
        print("     Adaptive Orchestrator v5 — Resource & Concurrency Ledger")
        print("=" * 65)
        budget_str = str(self.max_budget) if self.max_budget is not None else "Unbounded (v5 Policy)"
        print(f"  PHYSICAL WORKERS SPAWNED: {self.spawned_total} (Budget: {budget_str})")
        print(f"  ACTIVE PHYSICAL WORKERS:  {self.active_total} / {self.max_capacity} (AIMD Cap: {self.current_capacity})")
        print(f"  IDLE REUSABLE WORKERS:    {len(self.idle_workers)}")
        print(f"  WORKER REUSES:            {self.worker_reuses}")
        print(f"  TASKS DISPATCHED:         {self.tasks_dispatched}")
        print(f"  COORDINATOR ALLOCATIONS:  {self.coordinator_quotas}")
        print("-" * 65)
        if self.active_workers or self.idle_workers:
            if self.active_workers:
                print("  Active Workers:")
                for wid, info in self.active_workers.items():
                    print(f"    - [{wid}] {info['role']} ({info.get('domain', 'general')}) [Reuses: {info.get('reuses', 0)}]")
            if self.idle_workers:
                print("  Idle Pooled Workers (Warm Context):")
                for wid, info in self.idle_workers.items():
                    print(f"    - [{wid}] {info['role']} ({info.get('domain', 'general')})")
        else:
            print("  Workers: None active")
        print("=" * 65)

def main():
    parser = argparse.ArgumentParser(description="Adaptive Orchestrator v5 Resource & Concurrency Ledger")
    parser.add_argument("--status", action="store_true", help="Print ledger status format")
    parser.add_argument("--check", nargs=2, type=int, metavar=("SPAWNED", "ACTIVE"),
                        help="Check if spawn is permitted given (SPAWNED, ACTIVE)")
    parser.add_argument("--max-concurrent", type=int, default=DEFAULT_MAX_CAPACITY, help="Max physical concurrency")
    parser.add_argument("--max-budget", type=int, default=None, help="Optional external budget ceiling")
    parser.add_argument("--json", action="store_true", help="Output status as JSON")

    args = parser.parse_args()
    ledger = BudgetLedger(max_concurrent=args.max_concurrent, max_budget=args.max_budget)

    if args.check:
        spawned, active = args.check
        can_spawn = (args.max_budget is None or spawned < args.max_budget) and (active < args.max_concurrent)
        result = {
            "spawned": spawned,
            "active": active,
            "max_budget": args.max_budget,
            "max_concurrent": args.max_concurrent,
            "allowed": can_spawn,
            "reason": "OK" if can_spawn else ("Budget exhausted" if (args.max_budget and spawned >= args.max_budget) else "Capacity cap reached")
        }
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status_str = "ALLOWED" if can_spawn else f"DENIED ({result['reason']})"
            print(f"Spawn Check [{spawned} spawned, {active}/{args.max_concurrent} active]: {status_str}")
        sys.exit(0 if can_spawn else 1)

    if args.json:
        print(json.dumps(ledger.status_summary(), indent=2))
    else:
        ledger.print_ledger()

if __name__ == "__main__":
    main()
