import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from scripts.budget_ledger import BudgetLedger

class TestBudgetLedger(unittest.TestCase):
    def setUp(self):
        self.ledger = BudgetLedger()

    def test_initial_state(self):
        self.assertEqual(self.ledger.spawned_total, 0)
        self.assertEqual(self.ledger.active_total, 0)
        self.assertEqual(self.ledger.remaining_budget, 10)
        self.assertTrue(self.ledger.can_spawn(1))

    def test_concurrency_cap(self):
        for i in range(4):
            ok = self.ledger.spawn(f"worker-{i}", "Explorer", "Wave 1")
            self.assertTrue(ok)
        self.assertEqual(self.ledger.active_total, 4)
        self.assertEqual(self.ledger.spawned_total, 4)
        self.assertEqual(self.ledger.remaining_budget, 6)
        # 5th concurrent should be rejected
        self.assertFalse(self.ledger.can_spawn(1))
        self.assertFalse(self.ledger.spawn("worker-5", "Explorer", "Wave 1"))

    def test_termination_and_reuse(self):
        for i in range(4):
            self.ledger.spawn(f"worker-{i}", "Explorer", "Wave 1")
        self.ledger.terminate("worker-0")
        self.assertEqual(self.ledger.active_total, 3)
        self.assertTrue(self.ledger.can_spawn(1))
        ok = self.ledger.spawn("worker-4", "Implementer", "Wave 3")
        self.assertTrue(ok)
        self.assertEqual(self.ledger.spawned_total, 5)

    def test_mission_budget_cap(self):
        # Spawn and terminate up to 10 total
        for i in range(10):
            self.assertTrue(self.ledger.can_spawn(1))
            self.ledger.spawn(f"worker-{i}", "Explorer", "Wave 1")
            self.ledger.terminate(f"worker-{i}")
        self.assertEqual(self.ledger.spawned_total, 10)
        self.assertEqual(self.ledger.remaining_budget, 0)
        self.assertFalse(self.ledger.can_spawn(1))
        self.assertFalse(self.ledger.spawn("worker-11", "Explorer", "Wave 1"))

    def test_coordinator_quota_reservation(self):
        self.assertTrue(self.ledger.reserve_coordinator_quota("coord-1", 2))
        self.assertEqual(self.ledger.active_total, 2)
        self.assertTrue(self.ledger.spawn("worker-1", "Explorer", "Wave 1"))
        self.assertEqual(self.ledger.active_total, 3)
        self.ledger.release_coordinator_quota("coord-1")
        self.assertEqual(self.ledger.active_total, 1)

    def test_collapse_all(self):
        self.ledger.spawn("worker-1", "Explorer", "Wave 1")
        self.ledger.spawn("worker-2", "Explorer", "Wave 1")
        self.assertEqual(self.ledger.active_total, 2)
        count = self.ledger.collapse_all()
        self.assertEqual(count, 2)
        self.assertEqual(self.ledger.active_total, 0)

if __name__ == "__main__":
    unittest.main()
