import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.budget_ledger import BudgetLedger

class TestBudgetLedger(unittest.TestCase):
    def setUp(self):
        self.ledger = BudgetLedger(max_concurrent=4)

    def test_initial_state(self):
        self.assertEqual(self.ledger.spawned_total, 0)
        self.assertEqual(self.ledger.active_total, 0)
        self.assertEqual(self.ledger.worker_reuses, 0)
        self.assertTrue(self.ledger.can_spawn(1))

    def test_concurrency_cap(self):
        for i in range(4):
            ok = self.ledger.spawn(f"worker-{i}", "Explorer", "research")
            self.assertTrue(ok)
        self.assertEqual(self.ledger.active_total, 4)
        self.assertEqual(self.ledger.spawned_total, 4)
        # 5th concurrent should be rejected by capacity limit
        self.assertFalse(self.ledger.can_spawn(1))
        self.assertFalse(self.ledger.spawn("worker-5", "Explorer", "research"))

    def test_worker_idle_and_reuse(self):
        self.ledger.spawn("worker-0", "Explorer", "research")
        self.assertEqual(self.ledger.active_total, 1)
        self.assertTrue(self.ledger.release_to_idle("worker-0"))
        self.assertEqual(self.ledger.active_total, 0)
        self.assertEqual(len(self.ledger.idle_workers), 1)

        # Reuse without spawning
        self.assertTrue(self.ledger.record_reuse("worker-0", "task-2"))
        self.assertEqual(self.ledger.active_total, 1)
        self.assertEqual(self.ledger.worker_reuses, 1)
        self.assertEqual(self.ledger.spawned_total, 1)  # No extra spawn consumed!

    def test_optional_budget_cap(self):
        capped_ledger = BudgetLedger(max_concurrent=4, max_budget=10)
        for i in range(10):
            self.assertTrue(capped_ledger.can_spawn(1))
            capped_ledger.spawn(f"worker-{i}", "Explorer", "research")
            capped_ledger.terminate(f"worker-{i}")
        self.assertEqual(capped_ledger.spawned_total, 10)
        self.assertEqual(capped_ledger.remaining_budget, 0)
        self.assertFalse(capped_ledger.can_spawn(1))
        self.assertFalse(capped_ledger.spawn("worker-11", "Explorer", "research"))

    def test_unbounded_v5_budget(self):
        unbounded_ledger = BudgetLedger(max_concurrent=2, max_budget=None)
        for i in range(15):
            self.assertTrue(unbounded_ledger.can_spawn(1))
            unbounded_ledger.spawn(f"worker-{i}", "Worker", "general")
            unbounded_ledger.terminate(f"worker-{i}")
        self.assertEqual(unbounded_ledger.spawned_total, 15)
        self.assertTrue(unbounded_ledger.can_spawn(1))

    def test_coordinator_quota_reservation(self):
        self.assertTrue(self.ledger.reserve_coordinator_quota("coord-1", 2))
        self.assertEqual(self.ledger.active_total, 2)
        self.assertTrue(self.ledger.spawn("worker-1", "Explorer", "research"))
        self.assertEqual(self.ledger.active_total, 3)
        self.ledger.release_coordinator_quota("coord-1")
        self.assertEqual(self.ledger.active_total, 1)

    def test_collapse_all(self):
        self.ledger.spawn("worker-1", "Explorer", "research")
        self.ledger.spawn("worker-2", "Explorer", "research")
        self.assertEqual(self.ledger.active_total, 2)
        count = self.ledger.collapse_all()
        self.assertEqual(count, 2)
        self.assertEqual(self.ledger.active_total, 0)

if __name__ == "__main__":
    unittest.main()
