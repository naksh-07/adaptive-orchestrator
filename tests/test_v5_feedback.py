"""
Unit tests for Execution Feedback signals and FeedbackCollector.
Adaptive Orchestrator v5 - Phase 3.
"""

import unittest
from orchestrator.scheduler.feedback import FeedbackCollector, FeedbackSignal, FeedbackSignalType


class TestFeedbackCollector(unittest.TestCase):
    def setUp(self):
        self.collector = FeedbackCollector(window_size=10)

    def test_record_task_started(self):
        sig = self.collector.record_task_started("t1", "w1")
        self.assertEqual(sig.signal_type, FeedbackSignalType.TASK_STARTED)
        self.assertEqual(sig.task_id, "t1")
        self.assertEqual(sig.worker_id, "w1")
        self.assertEqual(len(self.collector.recent_signals()), 1)

    def test_record_task_completed_and_latency_metrics(self):
        self.collector.record_task_completed("t1", "w1", latency=1.5)
        self.collector.record_task_completed("t2", "w1", latency=2.5)

        self.assertEqual(self.collector.total_completed, 2)
        self.assertEqual(self.collector.consecutive_successes, 2)
        self.assertEqual(self.collector.consecutive_failures, 0)
        self.assertAlmostEqual(self.collector.average_latency, 2.0)

    def test_record_task_failed(self):
        self.collector.record_task_completed("t1", "w1", latency=1.0)
        self.assertEqual(self.collector.consecutive_successes, 1)

        # Failure resets consecutive successes
        sig = self.collector.record_task_failed("t2", "w1", error="Command failed")
        self.assertEqual(sig.signal_type, FeedbackSignalType.TASK_FAILED)
        self.assertEqual(self.collector.total_failed, 1)
        self.assertEqual(self.collector.consecutive_failures, 1)
        self.assertEqual(self.collector.consecutive_successes, 0)

    def test_record_rate_limit_error(self):
        sig = self.collector.record_task_failed(
            "t1",
            "w1",
            error="429 Resource Exhausted",
            is_capacity_error=True
        )
        self.assertEqual(sig.signal_type, FeedbackSignalType.RATE_LIMIT_ERROR)
        self.assertTrue(sig.is_capacity_error)
        self.assertEqual(self.collector.total_rate_limits, 1)
        self.assertEqual(self.collector.consecutive_failures, 1)

    def test_sliding_window_eviction(self):
        small_collector = FeedbackCollector(window_size=3)
        for i in range(5):
            small_collector.record_task_completed(f"t{i}", "w1")

        recent = small_collector.recent_signals()
        self.assertEqual(len(recent), 3)
        self.assertEqual([s.task_id for s in recent], ["t2", "t3", "t4"])

    def test_to_dict_serialization(self):
        self.collector.record_task_completed("t1", "w1", latency=2.0)
        d = self.collector.to_dict()
        self.assertEqual(d["total_completed"], 1)
        self.assertEqual(d["total_failed"], 0)
        self.assertEqual(d["total_rate_limits"], 0)
        self.assertEqual(d["average_latency"], 2.0)


if __name__ == "__main__":
    unittest.main()
