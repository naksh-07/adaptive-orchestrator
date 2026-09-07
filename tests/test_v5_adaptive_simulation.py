"""
End-to-End Simulation: 16+ Logical Tasks with Adaptive Concurrency & Intelligent Model Routing.
Adaptive Orchestrator v5 - Phase 3.

Demonstrates:
  - 16+ logical tasks in the DAG with wide parallel branches
  - 4 specialized reusable workers across 4 domains (backend, frontend, devops, testing)
  - Strict physical active concurrency governed dynamically by AIMD controller (C in [2, 8])
  - Decoupling: logical width (16 tasks) > physical active capacity (2 to 4 workers)
  - Intelligent Model Routing: FAST for standard code, PRO for high-priority & architecture & retries
  - Dynamic capacity scaling up on healthy completions, and backing off on rate-limit error
  - Warm worker reuse preserved: zero subagent destructions
"""

import unittest
from orchestrator.engine import MissionEngine
from orchestrator.models import EventType, MissionState, TaskState
from orchestrator.routing.models import ModelTier, RouterConfig
from orchestrator.routing.router import ModelRouter
from orchestrator.scheduler.aimd import AIMDConfig, AIMDController
from orchestrator.scheduler.scheduler import EventDrivenScheduler
from orchestrator.workers.adapter import ExecutionResult, MockExecutionAdapter
from orchestrator.workers.models import Worker


class TestAdaptiveConcurrencySimulation(unittest.TestCase):
    def test_16_task_dag_with_adaptive_aimd_and_model_routing(self):
        engine = MissionEngine(
            mission_id="sim_phase3_16",
            title="16-Task Scaled Enterprise Refactor with AIMD"
        )

        # 1. Configure AIMD Controller (min=2, max=6, initial=2, healthy_threshold=2)
        aimd = AIMDController(AIMDConfig(
            min_capacity=2,
            max_capacity=6,
            initial_capacity=2,
            increase_step=1,
            decrease_factor=0.5,
            healthy_threshold=2,
            cooldown_steps=1,
        ))

        # 2. Configure Model Router
        router = ModelRouter(RouterConfig(
            fast_model_id="gemini-2.5-flash",
            pro_model_id="gemini-2.5-pro",
            high_priority_threshold=8.5,
            pro_domains={"devops", "security"},
        ))

        # 3. Configure Mock Execution Adapter
        adapter = MockExecutionAdapter(default_success=True)

        scheduler = EventDrivenScheduler(
            ready_queue=engine.ready_queue,
            worker_registry=engine.workers,
            execution_adapter=adapter,
            aimd_controller=aimd,
            model_router=router,
        )
        engine.attach_scheduler(scheduler)

        # 4. Register 4 Reusable Workers across 4 Domains
        w_backend = Worker(worker_id="w_backend", domain="backend")
        w_frontend = Worker(worker_id="w_frontend", domain="frontend")
        w_devops = Worker(worker_id="w_devops", domain="devops")
        w_qa = Worker(worker_id="w_qa", domain="testing")

        engine.register_worker(w_backend)
        engine.register_worker(w_frontend)
        engine.register_worker(w_devops)
        engine.register_worker(w_qa)

        self.assertEqual(engine.workers.worker_count, 4)
        self.assertEqual(engine.workers.idle_count, 4)

        # 5. Build 16-Task DAG with Wide Parallel Roots and Dependent Branches
        # Tier 0: 6 Root Tasks (Zero Dependencies) -> Immediate ReadyQueue insertion
        engine.add_task(task_id="t_api_auth", title="Auth API Spec", domain="backend", priority=7.0)
        engine.add_task(task_id="t_api_billing", title="Billing API Spec", domain="backend", priority=6.0)
        engine.add_task(task_id="t_ui_nav", title="Navigation Bar", domain="frontend", priority=5.0)
        engine.add_task(task_id="t_ui_dashboard", title="Dashboard Layout", domain="frontend", priority=6.0)
        engine.add_task(task_id="t_k8s_manifests", title="Kubernetes Config", domain="devops", priority=9.0)  # High priority -> PRO
        engine.add_task(task_id="t_test_fixtures", title="Test Fixture Setup", domain="testing", priority=4.0)

        # Tier 1: 4 Intermediate Implementation Tasks
        engine.add_task(
            task_id="t_impl_auth",
            title="Implement Auth Service",
            domain="backend",
            priority=8.0,
            dependencies=["t_api_auth"]
        )
        engine.add_task(
            task_id="t_impl_billing",
            title="Implement Billing Service",
            domain="backend",
            priority=9.5,  # High priority -> PRO
            dependencies=["t_api_billing"]
        )
        engine.add_task(
            task_id="t_impl_ui",
            title="Connect UI Components",
            domain="frontend",
            priority=7.5,
            dependencies=["t_ui_nav", "t_ui_dashboard"]
        )
        engine.add_task(
            task_id="t_infra_helm",
            title="Helm Charts",
            domain="devops",
            priority=8.0,
            dependencies=["t_k8s_manifests"]
        )

        # Tier 2: 4 Verification & Integration Tasks
        engine.add_task(
            task_id="t_test_auth",
            title="Auth Unit Tests",
            domain="testing",
            priority=7.0,
            dependencies=["t_impl_auth", "t_test_fixtures"]
        )
        engine.add_task(
            task_id="t_test_billing",
            title="Billing Unit Tests",
            domain="testing",
            priority=8.0,
            dependencies=["t_impl_billing", "t_test_fixtures"]
        )
        engine.add_task(
            task_id="t_e2e_frontend",
            title="Frontend Cypress Suite",
            domain="frontend",
            priority=6.0,
            dependencies=["t_impl_ui"]
        )
        engine.add_task(
            task_id="t_ci_pipeline",
            title="CI Automation Pipeline",
            domain="devops",
            priority=7.0,
            dependencies=["t_infra_helm"]
        )

        # Tier 3: 2 Final Release & Audit Leaves
        engine.add_task(
            task_id="t_integration_e2e",
            title="End-to-End System Tests",
            domain="testing",
            priority=9.0,  # High priority -> PRO
            dependencies=["t_test_auth", "t_test_billing", "t_e2e_frontend"]
        )
        engine.add_task(
            task_id="t_release_gate",
            title="Production Release Gate",
            domain="testing",
            priority=9.8,  # High priority -> PRO
            dependencies=["t_integration_e2e", "t_ci_pipeline"]
        )

        # Verify DAG properties: 16 tasks total
        self.assertEqual(engine.graph.task_count(), 16)

        # Initial ready tasks count = 6 roots (Logical width = 6)
        self.assertEqual(len(engine.ready_queue), 6)

        # Invariant: Logical width (6 ready) exceeds initial physical capacity (2)
        self.assertGreater(len(engine.ready_queue), aimd.current_capacity)

        # Simulate a transient rate limit on t_impl_auth to test AIMD backoff and recovery
        adapter.rate_limit_tasks.add("t_impl_auth")

        # 6. Execute Mission
        # Because the adapter simulates synchronous mock completions, the scheduler loop
        # continuously drives execution, unlocks dependents, adapts capacity, and routes models.
        engine.start_mission()

        # 7. Verification of Mission Completion
        # Note: t_impl_auth encountered rate limit and failed. Dependents of t_impl_auth (t_test_auth, etc.)
        # are BLOCKED as expected under the dependency resolver.
        # Let's inspect the execution metrics.
        tasks = engine.graph.all_tasks()
        self.assertEqual(len(tasks), 16)

        # Confirm capacity decreased when rate limit hit
        self.assertGreaterEqual(aimd.state.total_decreases, 1)

        # Total dispatches occurred
        self.assertGreater(len(scheduler.dispatches), 8)

        # Check model routing distribution across dispatches
        tiers = [d.execution_profile.tier for d in scheduler.dispatches if d.execution_profile]
        self.assertIn(ModelTier.PRO, tiers)
        self.assertIn(ModelTier.FAST, tiers)

        # Specifically verify high priority tasks routed to PRO
        for d in scheduler.dispatches:
            if d.task_id == "t_k8s_manifests":
                self.assertEqual(d.execution_profile.tier, ModelTier.PRO)
                self.assertEqual(d.execution_profile.model_id, "gemini-2.5-pro")
            elif d.task_id == "t_ui_nav":
                self.assertEqual(d.execution_profile.tier, ModelTier.FAST)
                self.assertEqual(d.execution_profile.model_id, "gemini-2.5-flash")

        # Check worker reuse count: 4 workers executed multiple tasks without destruction
        self.assertEqual(adapter.spawn_count, 4)
        self.assertGreater(adapter.reuse_count, 0)
        self.assertEqual(adapter.reuse_count + adapter.spawn_count, len(scheduler.dispatches))


if __name__ == "__main__":
    unittest.main()
