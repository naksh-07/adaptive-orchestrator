"""
Deterministic Multi-Worker Event-Driven Simulation.
Adaptive Orchestrator v5 - Phase 2.

Demonstrates:
  - Multiple domain workers (backend, frontend, testing)
  - Multiple independent ready tasks executing without wave barriers
  - Immediate downstream dependency unlock & follow-up dispatch
  - Reusable workers sustaining continuous flow across 8 tasks
"""

import unittest
from orchestrator.engine import MissionEngine
from orchestrator.models import MissionState, TaskState
from orchestrator.scheduler.scheduler import EventDrivenScheduler
from orchestrator.workers.adapter import MockExecutionAdapter
from orchestrator.workers.models import Worker, WorkerState


class TestContinuousExecutionSimulation(unittest.TestCase):
    def test_multi_domain_continuous_dag_simulation(self):
        """
        Executes an 8-task multi-domain DAG using 3 reusable workers:
        - worker_backend (domain: 'backend')
        - worker_frontend (domain: 'frontend')
        - worker_qa (domain: 'testing')

        DAG topology:
          t_api_spec (backend)   t_db_schema (backend)   t_ui_mock (frontend)
                 \\                    /                     /
                  \\                  /                     /
                     t_api_impl (backend)                  /
                             |                            /
                             |              t_ui_client (frontend)
                             |                       /
                        t_unit_tests (testing)      /
                                    \\             /
                                     \\           /
                                  t_e2e_tests (testing)
                                            |
                                  t_release_audit (testing)
        """
        engine = MissionEngine(mission_id="sim_2026_01", title="Continuous Execution Simulation")

        # Custom mock adapter tracking detailed timeline
        adapter = MockExecutionAdapter(default_success=True)

        scheduler = EventDrivenScheduler(
            ready_queue=engine.ready_queue,
            worker_registry=engine.workers,
            execution_adapter=adapter,
        )
        engine.attach_scheduler(scheduler)

        # 1. Register 3 specialized domain workers
        w_backend = Worker(worker_id="w_backend", domain="backend")
        w_frontend = Worker(worker_id="w_frontend", domain="frontend")
        w_qa = Worker(worker_id="w_qa", domain="testing")

        engine.register_worker(w_backend)
        engine.register_worker(w_frontend)
        engine.register_worker(w_qa)

        self.assertEqual(engine.workers.worker_count, 3)
        self.assertEqual(engine.workers.idle_count, 3)

        # 2. Add DAG tasks
        # Roots (zero initial dependencies)
        engine.add_task(task_id="t_api_spec", title="API Specification", domain="backend", priority=10.0)
        engine.add_task(task_id="t_db_schema", title="Database Schema", domain="backend", priority=9.0)
        engine.add_task(task_id="t_ui_mock", title="UI Mockups", domain="frontend", priority=9.0)

        # Intermediates
        engine.add_task(
            task_id="t_api_impl",
            title="API Implementation",
            domain="backend",
            priority=8.0,
            dependencies=["t_api_spec", "t_db_schema"]
        )
        engine.add_task(
            task_id="t_ui_client",
            title="Frontend API Client",
            domain="frontend",
            priority=7.0,
            dependencies=["t_api_spec", "t_ui_mock"]
        )
        engine.add_task(
            task_id="t_unit_tests",
            title="Backend Unit Tests",
            domain="testing",
            priority=8.0,
            dependencies=["t_api_impl"]
        )

        # Leaves
        engine.add_task(
            task_id="t_e2e_tests",
            title="End-to-End Tests",
            domain="testing",
            priority=6.0,
            dependencies=["t_ui_client", "t_unit_tests"]
        )
        engine.add_task(
            task_id="t_release_audit",
            title="Final Release Audit",
            domain="testing",
            priority=5.0,
            dependencies=["t_e2e_tests"]
        )

        self.assertEqual(engine.graph.task_count(), 8)

        # 3. Start Mission
        engine.start_mission()

        # 4. Assert all 8 tasks completed successfully
        self.assertEqual(engine.mission.state, MissionState.COMPLETED)
        for task in engine.graph.all_tasks():
            self.assertEqual(task.status, TaskState.PASSED, f"Task {task.task_id} did not pass!")

        # 5. Verify worker reuse metrics
        # 8 total tasks executed across only 3 workers
        self.assertEqual(len(scheduler.dispatches), 8)
        self.assertEqual(adapter.spawn_count, 3)  # Exactly 3 initial spawns (1 per worker)
        self.assertEqual(adapter.reuse_count, 5)  # 5 worker reuses via native wake!

        # Detailed worker metrics
        self.assertEqual(w_backend.metrics.tasks_completed, 3)  # t_api_spec, t_db_schema, t_api_impl
        self.assertEqual(w_frontend.metrics.tasks_completed, 2)  # t_ui_mock, t_ui_client
        self.assertEqual(w_qa.metrics.tasks_completed, 3)        # t_unit_tests, t_e2e_tests, t_release_audit

        # All workers are warm and returned to IDLE
        self.assertEqual(engine.workers.idle_count, 3)
        self.assertEqual(engine.workers.busy_count, 0)
        self.assertTrue(w_backend.is_idle)
        self.assertTrue(w_frontend.is_idle)
        self.assertTrue(w_qa.is_idle)

        # Invariant check: No worker destruction or re-instantiation occurred
        self.assertEqual(w_backend.task_history, ["t_api_spec", "t_db_schema", "t_api_impl"])
        self.assertEqual(w_frontend.task_history, ["t_ui_mock", "t_ui_client"])
        self.assertEqual(w_qa.task_history, ["t_unit_tests", "t_e2e_tests", "t_release_audit"])

        # 6. Verify strict domain affinity routing
        for dispatch in scheduler.dispatches:
            if dispatch.domain == "backend":
                self.assertEqual(dispatch.worker_id, "w_backend")
            elif dispatch.domain == "frontend":
                self.assertEqual(dispatch.worker_id, "w_frontend")
            elif dispatch.domain == "testing":
                self.assertEqual(dispatch.worker_id, "w_qa")


if __name__ == "__main__":
    unittest.main()
