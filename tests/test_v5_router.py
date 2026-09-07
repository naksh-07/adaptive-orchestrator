"""
Unit tests for the Intelligent Model Router.
Adaptive Orchestrator v5 - Phase 3.
"""

import unittest
from orchestrator.models import Task, TaskState
from orchestrator.routing.models import ExecutionProfile, ModelTier, RouterConfig
from orchestrator.routing.router import ModelRouter
from orchestrator.workers.models import Worker


class TestModelRouter(unittest.TestCase):
    def setUp(self):
        self.config = RouterConfig(
            fast_model_id="model: flash",
            pro_model_id="model: pro",
            high_priority_threshold=8.0,
            max_retries_before_escalation=1,
            pro_domains={"architecture", "audit", "security"},
        )
        self.router = ModelRouter(self.config)

    def test_routine_implementation_task_routes_to_fast(self):
        t1 = Task(
            task_id="t1",
            mission_id="m1",
            title="Implement helper function",
            domain="backend",
            priority=5.0,
        )
        profile = self.router.route(t1)
        self.assertEqual(profile.tier, ModelTier.FAST)
        self.assertEqual(profile.model_id, "model: flash")
        self.assertIn("Standard", profile.routing_reason)

    def test_high_priority_task_escalates_to_pro(self):
        t2 = Task(
            task_id="t2",
            mission_id="m1",
            title="Critical database migration",
            domain="backend",
            priority=9.5,
        )
        profile = self.router.route(t2)
        self.assertEqual(profile.tier, ModelTier.PRO)
        self.assertEqual(profile.model_id, "model: pro")
        self.assertIn("High task priority", profile.routing_reason)

    def test_pro_domain_routes_to_pro(self):
        t_arch = Task(
            task_id="t_arch",
            mission_id="m1",
            title="System Architecture Synthesis",
            domain="architecture",
            priority=4.0,
        )
        profile = self.router.route(t_arch)
        self.assertEqual(profile.tier, ModelTier.PRO)
        self.assertEqual(profile.model_id, "model: pro")
        self.assertIn("Domain 'architecture'", profile.routing_reason)

    def test_retry_escalates_to_pro(self):
        t_retry = Task(
            task_id="t_retry",
            mission_id="m1",
            title="Buggy Component",
            domain="frontend",
            priority=3.0,
            retry_count=1,
        )
        profile = self.router.route(t_retry)
        self.assertEqual(profile.tier, ModelTier.PRO)
        self.assertEqual(profile.model_id, "model: pro")
        self.assertIn("Escalated to PRO", profile.routing_reason)

    def test_adversarial_review_flag_escalates_to_pro(self):
        t_adv = Task(
            task_id="t_adv",
            mission_id="m1",
            title="Security Audit Verification",
            domain="testing",
            priority=5.0,
            metadata={"adversarial_review": True},
        )
        profile = self.router.route(t_adv)
        self.assertEqual(profile.tier, ModelTier.PRO)
        self.assertIn("Adversarial verification", profile.routing_reason)

    def test_explicit_tier_override(self):
        t_override = Task(
            task_id="t_override",
            mission_id="m1",
            title="Task with override",
            domain="backend",
            priority=1.0,
            metadata={"model_tier": "PRO"},
        )
        profile = self.router.route(t_override)
        self.assertEqual(profile.tier, ModelTier.PRO)
        self.assertIn("Explicit task metadata tier", profile.routing_reason)

    def test_custom_model_identifiers_in_config(self):
        custom_cfg = RouterConfig(
            fast_model_id="gemini-2.5-flash",
            pro_model_id="gemini-2.5-pro",
        )
        custom_router = ModelRouter(custom_cfg)

        t_fast = Task(task_id="t1", mission_id="m1", title="Fast task", domain="general")
        t_pro = Task(task_id="t2", mission_id="m1", title="Pro task", domain="architecture")

        self.assertEqual(custom_router.route(t_fast).model_id, "gemini-2.5-flash")
        self.assertEqual(custom_router.route(t_pro).model_id, "gemini-2.5-pro")

    def test_deterministic_routing_decisions(self):
        t = Task(task_id="t1", mission_id="m1", title="Deterministic task", domain="backend")
        profile1 = self.router.route(t)
        profile2 = self.router.route(t)
        self.assertEqual(profile1.tier, profile2.tier)
        self.assertEqual(profile1.model_id, profile2.model_id)
        self.assertEqual(profile1.routing_reason, profile2.routing_reason)


if __name__ == "__main__":
    unittest.main()
