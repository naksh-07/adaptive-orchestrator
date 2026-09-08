"""
Adaptive Orchestrator v5 - Section 11: Small-Mission Regression
Verifies that trivial and small tasks remain fast, low-overhead, and unbloated:
- one-task mission
- two-task dependency chain
- two independent tasks
- one failed task
- one verification-required task
"""

import time
import unittest

from orchestrator.engine import MissionEngine
from orchestrator.models import MissionState, Task, TaskState
from orchestrator.verification import Tier1SelfTestVerifier


class TestV5SmallMissionRegression(unittest.TestCase):
    def test_one_task_mission(self):
        """Single-task mission completes with minimal overhead."""
        start = time.perf_counter()

        engine = MissionEngine(mission_id="m_single", title="Single Task Mission")
        t1 = Task(id="t1", domain="backend")
        engine.add_task(t1)
        engine.start()

        engine.register_worker(worker_id="w1", domains=["backend"])
        d = engine.assign_next()
        self.assertEqual(d.task_id, "t1")

        engine.mark_task_completed("t1", result={"val": "ok"})
        self.assertEqual(engine.state, MissionState.COMPLETED)

        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 0.5)  # Completes in sub-second

    def test_two_task_dependency_chain(self):
        """Sequential 2-task chain (t1 -> t2) resolves cleanly."""
        engine = MissionEngine(mission_id="m_chain", title="Two-Task Chain")
        t1 = Task(id="t1", domain="backend")
        t2 = Task(id="t2", domain="backend", dependencies={"t1"})

        engine.add_task(t1)
        engine.add_task(t2)
        engine.start()

        engine.register_worker(worker_id="w1", domains=["backend"])

        # Step 1: t1 ready, t2 blocked
        d1 = engine.assign_next()
        self.assertEqual(d1.task_id, "t1")
        self.assertIsNone(engine.assign_next())

        # Complete t1 -> t2 unblocked
        engine.mark_task_completed("t1")
        self.assertIn("t2", engine.get_ready_tasks())

        # Step 2: dispatch t2
        d2 = engine.assign_next()
        self.assertEqual(d2.task_id, "t2")
        engine.mark_task_completed("t2")

        self.assertEqual(engine.state, MissionState.COMPLETED)

    def test_two_independent_tasks(self):
        """Two disjoint independent tasks execute and complete in parallel."""
        engine = MissionEngine(mission_id="m_parallel", title="Two Independent Tasks")
        t1 = Task(id="t1", domain="backend")
        t2 = Task(id="t2", domain="frontend")

        engine.add_task(t1)
        engine.add_task(t2)
        engine.start()

        engine.register_worker(worker_id="w_be", domains=["backend"])
        engine.register_worker(worker_id="w_fe", domains=["frontend"])

        # Both tasks ready immediately
        ready = engine.get_ready_tasks()
        ready_ids = {t.task_id if hasattr(t, "task_id") else str(t) for t in ready}
        self.assertEqual(ready_ids, {"t1", "t2"})

        d1 = engine.assign_next()
        d2 = engine.assign_next()
        self.assertIsNotNone(d1)
        self.assertIsNotNone(d2)
        self.assertEqual({d1.task_id, d2.task_id}, {"t1", "t2"})

        engine.mark_task_completed(d1.task_id)
        engine.mark_task_completed(d2.task_id)
        self.assertEqual(engine.state, MissionState.COMPLETED)

    def test_one_failed_task(self):
        """Single failing task halts gracefully without loop or hang."""
        engine = MissionEngine(mission_id="m_one_fail", title="One Failed Task")
        t1 = Task(id="t1", domain="backend", max_retries=1)
        engine.add_task(t1)
        engine.start()

        engine.register_worker(worker_id="w1", domains=["backend"])
        engine.assign_next()

        # Fail permanently
        engine.mark_task_failed("t1", error="Fatal non-retryable error", can_retry=False)
        self.assertEqual(t1.status, TaskState.FAILED)

        # Run victory audit -> mission fails
        audit = engine.run_victory_audit()
        self.assertFalse(audit.passed)
        self.assertEqual(engine.state, MissionState.FAILED)

    def test_one_verification_required_task(self):
        """Single task with verification completes and confirms victory."""
        engine = MissionEngine(mission_id="m_one_verif", title="One Verification Task")
        t1 = Task(id="t1", domain="backend", write_set={"file.py"})
        engine.add_task(t1)
        engine.start()

        engine.register_worker(worker_id="w1", domains=["backend"])
        d = engine.assign_next()

        # Run Tier 1 self-test verification
        verifier = Tier1SelfTestVerifier()
        v_res = verifier.verify(t1)
        self.assertTrue(v_res.passed)

        engine.mark_task_completed("t1", result={"verified": True})
        self.assertEqual(engine.state, MissionState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
