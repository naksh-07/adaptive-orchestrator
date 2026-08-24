#!/usr/bin/env python3
"""
Adaptive Orchestrator — Budget & Concurrency Ledger (v4 Foundation)
Tracks and enforces hard limits:
- Max 4 concurrent subagents tree-wide
- Max 10 total launches per mission (Root + Children + Grandchildren)
"""

import sys
import json
import argparse
from typing import Dict, List, Optional, Any

MAX_CONCURRENT: int = 4
MAX_TOTAL_LAUNCHES: int = 10

class BudgetLedger:
    def __init__(self, max_concurrent: int = MAX_CONCURRENT, max_budget: int = MAX_TOTAL_LAUNCHES):
        self.max_concurrent = max_concurrent
        self.max_budget = max_budget
        self.spawned_total: int = 0
        self.active_workers: Dict[str, Dict[str, Any]] = {}
        self.coordinator_quotas: Dict[str, int] = {}
        self.history: List[Dict[str, Any]] = []

    @property
    def active_total(self) -> int:
        """Calculates active direct leaves + reserved active coordinator allocations."""
        direct_active = len(self.active_workers)
        reserved_coords = sum(self.coordinator_quotas.values())
        return direct_active + reserved_coords

    @property
    def remaining_budget(self) -> int:
        return max(0, self.max_budget - self.spawned_total)

    def can_spawn(self, count: int = 1) -> bool:
        if self.spawned_total + count > self.max_budget:
            return False
        if self.active_total + count > self.max_concurrent:
            return False
        return True

    def spawn(self, worker_id: str, role: str, wave: str = "Wave 1", coordinator_id: Optional[str] = None) -> bool:
        if not self.can_spawn(1):
            return False
        self.spawned_total += 1
        record = {
            "id": worker_id,
            "role": role,
            "wave": wave,
            "coordinator_id": coordinator_id,
            "status": "ACTIVE"
        }
        self.active_workers[worker_id] = record
        self.history.append(record)
        return True

    def terminate(self, worker_id: str) -> bool:
        if worker_id in self.active_workers:
            self.active_workers[worker_id]["status"] = "TERMINATED"
            del self.active_workers[worker_id]
            return True
        return False

    def reserve_coordinator_quota(self, coordinator_id: str, quota: int) -> bool:
        if not self.can_spawn(quota):
            return False
        self.coordinator_quotas[coordinator_id] = self.coordinator_quotas.get(coordinator_id, 0) + quota
        return True

    def release_coordinator_quota(self, coordinator_id: str, unused_quota: int = 0) -> None:
        if coordinator_id in self.coordinator_quotas:
            del self.coordinator_quotas[coordinator_id]

    def collapse_all(self) -> int:
        terminated_count = len(self.active_workers)
        self.active_workers.clear()
        self.coordinator_quotas.clear()
        return terminated_count

    def status_summary(self) -> Dict[str, Any]:
        return {
            "spawned_total": self.spawned_total,
            "active_total": self.active_total,
            "direct_active_count": len(self.active_workers),
            "reserved_coordinator_slots": sum(self.coordinator_quotas.values()),
            "remaining_budget": self.remaining_budget,
            "max_concurrent": self.max_concurrent,
            "max_budget": self.max_budget,
            "can_spawn_more": self.can_spawn(1)
        }

    def print_ledger(self) -> None:
        print("=" * 60)
        print("     Adaptive Orchestrator v4 — Mission Budget Ledger")
        print("=" * 60)
        print(f"  SPAWNED_TOTAL:           {self.spawned_total} / {self.max_budget}")
        print(f"  ACTIVE_TOTAL:            {self.active_total} / {self.max_concurrent} (Cap: {self.max_concurrent} max)")
        print(f"  REMAINING_BUDGET:        {self.remaining_budget}")
        print(f"  COORDINATOR_QUOTAS:      {self.coordinator_quotas}")
        print("-" * 60)
        if self.active_workers:
            print("  Active Workers:")
            for wid, info in self.active_workers.items():
                print(f"    - [{wid}] {info['role']} ({info['wave']})")
        else:
            print("  Active Workers: None (Workforce Collapsed)")
        print("=" * 60)

def main():
    parser = argparse.ArgumentParser(description="Adaptive Orchestrator Budget & Concurrency Calculator")
    parser.add_argument("--status", action="store_true", help="Print ledger status format")
    parser.add_argument("--check", nargs=2, type=int, metavar=("SPAWNED", "ACTIVE"),
                        help="Check if spawn is permitted given (SPAWNED, ACTIVE)")
    parser.add_argument("--json", action="store_true", help="Output status as JSON")

    args = parser.parse_args()
    ledger = BudgetLedger()

    if args.check:
        spawned, active = args.check
        can_spawn = (spawned < MAX_TOTAL_LAUNCHES) and (active < MAX_CONCURRENT)
        result = {
            "spawned": spawned,
            "active": active,
            "max_budget": MAX_TOTAL_LAUNCHES,
            "max_concurrent": MAX_CONCURRENT,
            "allowed": can_spawn,
            "reason": "OK" if can_spawn else ("Budget exhausted" if spawned >= MAX_TOTAL_LAUNCHES else "Concurrency cap reached")
        }
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status_str = "ALLOWED" if can_spawn else f"DENIED ({result['reason']})"
            print(f"Spawn Check [{spawned}/10 spawned, {active}/4 active]: {status_str}")
        sys.exit(0 if can_spawn else 1)

    if args.json:
        print(json.dumps(ledger.status_summary(), indent=2))
    else:
        ledger.print_ledger()

if __name__ == "__main__":
    main()
