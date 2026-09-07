"""
Unit tests for the AIMD Adaptive Concurrency Controller.
Adaptive Orchestrator v5 - Phase 3.
"""

import unittest
from orchestrator.scheduler.aimd import AIMDConfig, AIMDController, AIMDState
from orchestrator.scheduler.feedback import FeedbackSignal, FeedbackSignalType


class TestAIMDController(unittest.TestCase):
    def test_aimd_config_validation(self):
        # Default config valid
        cfg = AIMDConfig()
        self.assertEqual(cfg.min_capacity, 2)
        self.assertEqual(cfg.max_capacity, 8)
        self.assertEqual(cfg.initial_capacity, 4)

        # Invalid bounds
        with self.assertRaises(ValueError):
            AIMDConfig(min_capacity=0)

        with self.assertRaises(ValueError):
            AIMDConfig(min_capacity=5, max_capacity=3)

        with self.assertRaises(ValueError):
            AIMDConfig(min_capacity=2, max_capacity=8, initial_capacity=1)

        with self.assertRaises(ValueError):
            AIMDConfig(decrease_factor=1.5)

    def test_initial_capacity_and_dispatch_gate(self):
        controller = AIMDController(AIMDConfig(min_capacity=2, max_capacity=6, initial_capacity=3))
        self.assertEqual(controller.current_capacity, 3)
        self.assertFalse(controller.in_cooldown)

        # Safety gate checks
        self.assertTrue(controller.can_dispatch(active_tasks=0))
        self.assertTrue(controller.can_dispatch(active_tasks=2))
        self.assertFalse(controller.can_dispatch(active_tasks=3))
        self.assertFalse(controller.can_dispatch(active_tasks=4))

    def test_additive_increase_after_healthy_threshold(self):
        # Requires 3 consecutive healthy completions to increase capacity
        cfg = AIMDConfig(min_capacity=2, max_capacity=5, initial_capacity=2, increase_step=1, healthy_threshold=3)
        controller = AIMDController(cfg)

        self.assertEqual(controller.current_capacity, 2)

        # Signal 1: healthy completion -> no change yet
        sig = FeedbackSignal(signal_type=FeedbackSignalType.TASK_COMPLETED)
        cap = controller.process_feedback(sig)
        self.assertEqual(cap, 2)
        self.assertEqual(controller.state.consecutive_successes, 1)

        # Signal 2: healthy completion -> no change yet
        cap = controller.process_feedback(sig)
        self.assertEqual(cap, 2)
        self.assertEqual(controller.state.consecutive_successes, 2)

        # Signal 3: threshold reached -> capacity increases by 1!
        cap = controller.process_feedback(sig)
        self.assertEqual(cap, 3)
        self.assertEqual(controller.state.consecutive_successes, 0)
        self.assertEqual(controller.state.total_increases, 1)

    def test_max_capacity_clamp(self):
        cfg = AIMDConfig(min_capacity=2, max_capacity=3, initial_capacity=3, increase_step=1, healthy_threshold=1)
        controller = AIMDController(cfg)

        self.assertEqual(controller.current_capacity, 3)

        # Even with healthy signal, capacity should never exceed max_capacity
        sig = FeedbackSignal(signal_type=FeedbackSignalType.TASK_COMPLETED)
        cap = controller.process_feedback(sig)
        self.assertEqual(cap, 3)
        self.assertEqual(controller.current_capacity, 3)

    def test_multiplicative_decrease_on_rate_limit(self):
        cfg = AIMDConfig(min_capacity=2, max_capacity=8, initial_capacity=6, decrease_factor=0.5, cooldown_steps=1)
        controller = AIMDController(cfg)

        self.assertEqual(controller.current_capacity, 6)

        # Rate limit signal: floor(6 * 0.5) = 3
        sig = FeedbackSignal(signal_type=FeedbackSignalType.RATE_LIMIT_ERROR, error="HTTP 429: Too Many Requests")
        cap = controller.process_feedback(sig)
        self.assertEqual(cap, 3)
        self.assertTrue(controller.in_cooldown)
        self.assertEqual(controller.state.total_decreases, 1)

    def test_min_capacity_clamp(self):
        cfg = AIMDConfig(min_capacity=2, max_capacity=8, initial_capacity=2, decrease_factor=0.5)
        controller = AIMDController(cfg)

        self.assertEqual(controller.current_capacity, 2)

        # Rate limit when at min: floor(2 * 0.5) = 1, but clamped to min_capacity (2)
        sig = FeedbackSignal(signal_type=FeedbackSignalType.RATE_LIMIT_ERROR, error="HTTP 429")
        cap = controller.process_feedback(sig)
        self.assertEqual(cap, 2)
        self.assertEqual(controller.current_capacity, 2)

    def test_cooldown_hold_after_decrease(self):
        cfg = AIMDConfig(
            min_capacity=2,
            max_capacity=8,
            initial_capacity=6,
            decrease_factor=0.5,
            healthy_threshold=1,
            cooldown_steps=2,
        )
        controller = AIMDController(cfg)

        # Trigger decrease
        sig_rl = FeedbackSignal(signal_type=FeedbackSignalType.RATE_LIMIT_ERROR)
        controller.process_feedback(sig_rl)
        self.assertEqual(controller.current_capacity, 3)
        self.assertEqual(controller.state.cooldown_remaining, 2)

        # Successful completion during cooldown should decrement cooldown but NOT increase capacity
        sig_ok = FeedbackSignal(signal_type=FeedbackSignalType.TASK_COMPLETED)
        cap = controller.process_feedback(sig_ok)
        self.assertEqual(cap, 3)
        self.assertEqual(controller.state.cooldown_remaining, 1)

        cap = controller.process_feedback(sig_ok)
        self.assertEqual(cap, 3)
        self.assertEqual(controller.state.cooldown_remaining, 0)
        self.assertFalse(controller.in_cooldown)

        # Now cooldown expired: next healthy completion increases capacity!
        cap = controller.process_feedback(sig_ok)
        self.assertEqual(cap, 4)

    def test_repeated_task_failures_trigger_congestion_backoff(self):
        cfg = AIMDConfig(min_capacity=2, max_capacity=8, initial_capacity=6, healthy_threshold=2)
        controller = AIMDController(cfg)

        # Single task failure: tracks count but no decrease yet
        sig_fail = FeedbackSignal(signal_type=FeedbackSignalType.TASK_FAILED, error="SyntaxError")
        cap = controller.process_feedback(sig_fail)
        self.assertEqual(cap, 6)
        self.assertEqual(controller.state.consecutive_failures, 1)

        # Second consecutive task failure reaches threshold -> triggers multiplicative decrease
        cap = controller.process_feedback(sig_fail)
        self.assertEqual(cap, 3)
        self.assertEqual(controller.state.consecutive_failures, 0)

    def test_deterministic_force_and_reset(self):
        controller = AIMDController()
        controller.force_capacity(7)
        self.assertEqual(controller.current_capacity, 7)

        controller.force_capacity(20)  # Clamped to max (8)
        self.assertEqual(controller.current_capacity, 8)

        controller.force_capacity(0)   # Clamped to min (2)
        self.assertEqual(controller.current_capacity, 2)

        controller.reset()
        self.assertEqual(controller.current_capacity, 4)


if __name__ == "__main__":
    unittest.main()
