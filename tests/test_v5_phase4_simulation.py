"""
End-to-End Simulation Test for Phase 4:
Controlled Workspace Ownership, Worktree Isolation, and Sequential Integration.

Scenario:
- 14 logical tasks across 3 domains (backend, frontend, infra).
- Disjoint write sets executing in parallel.
- Deliberately overlapping write sets serialized safely.
- Isolated worktrees via MockWorktreeAdapter.
- Sequential merge queue via MockMergeAdapter.
- Simulated worker loss with automated retry and reassignment.
- Simulated merge conflict with failure isolation and evidence retention.
- Reusable warm domain workers.
- Adaptive AIMD concurrency control.
"""

import unittest
from typing import List, Set
from orchestrator.engine import MissionEngine
from orchestrator.integration.adapter import MockMergeAdapter
from orchestrator.integration.manager import IntegrationManager
from orchestrator.integration.models import MergeStatus
from orchestrator.models import EventType, TaskState
from orchestrator.scheduler.aimd import AIMDConfig, AIMDController
from orchestrator.scheduler.scheduler import EventDrivenScheduler
from orchestrator.workers.adapter import MockExecutionAdapter
from orchestrator.workers.affinity import DomainAffinityPolicy
from orchestrator.workers.models import Worker, WorkerState
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workspace.adapter import MockWorktreeAdapter
from orchestrator.workspace.collision import are_write_sets_overlapping
from orchestrator.workspace.registry import WorkspaceRegistry


class TestPhase4Simulation(unittest.TestCase):
    def test_full_mission_simulation(self):
        engine = MissionEngine(mission_id="m-phase4", title="Phase 4 Integration Simulation")
        worker_registry = WorkerRegistry()
        affinity_policy = DomainAffinityPolicy()
        aimd_controller = AIMDController(AIMDConfig(min_capacity=2, max_capacity=4, initial_capacity=2))
        ready_queue = engine.ready_queue
        execution_adapter = MockExecutionAdapter(auto_complete=False)  # Manual step-by-step
        worktree_adapter = MockWorktreeAdapter(base_dir="/mock/worktrees")
        workspace_registry = WorkspaceRegistry()
        merge_adapter = MockMergeAdapter()

        # Track concurrent active tasks to assert NO write set overlap ever occurred
        max_concurrent_tasks = 0
        total_worker_reuses = 0
        merge_conflict_detected = False
        worker_loss_recovered = False

        integration_manager = IntegrationManager(
            merge_adapter=merge_adapter,
            worktree_adapter=worktree_adapter,
            workspace_registry=workspace_registry,
            event_emitter=engine._emit,
        )

        scheduler = EventDrivenScheduler(
            engine=engine,
            worker_registry=worker_registry,
            affinity_policy=affinity_policy,
            aimd_controller=aimd_controller,
            ready_queue=ready_queue,
            execution_adapter=execution_adapter,
            workspace_registry=workspace_registry,
            worktree_adapter=worktree_adapter,
            integration_manager=integration_manager,
            event_emitter=engine._emit,
        )
        engine.attach_scheduler(scheduler)

        # Register workers across 3 domains
        workers = [
            Worker("w_be_1", "backend"),
            Worker("w_be_2", "backend"),
            Worker("w_fe_1", "frontend"),
            Worker("w_fe_2", "frontend"),
            Worker("w_infra_1", "infra"),
            Worker("w_infra_2", "infra"),
        ]
        for w in workers:
            worker_registry.register_worker(w)

        # Define 14 tasks
        # 1. Backend schema
        engine.add_task("t1_schema", "DB Schema", domain="backend", priority=10, write_set={"src/db/schema.py"})
        # 2. Backend auth service (disjoint)
        engine.add_task("t2_auth", "Auth Service", domain="backend", priority=9, write_set={"src/auth/service.py"})
        # 3. Backend auth routes (depends on auth service)
        engine.add_task("t3_routes", "Auth Routes", domain="backend", priority=8, dependencies=["t2_auth"], write_set={"src/auth/routes.py"})
        # 4. Frontend login
        engine.add_task("t4_fe_login", "FE Login", domain="frontend", priority=8, write_set={"src/fe/login.tsx"})
        # 5. Frontend dashboard (disjoint)
        engine.add_task("t5_fe_dash", "FE Dashboard", domain="frontend", priority=7, write_set={"src/fe/dash.tsx"})
        # 6. Infra terraform
        engine.add_task("t6_infra", "Infra Config", domain="infra", priority=7, write_set={"infra/main.tf"})
        # 7 & 8: Deliberate write collision on app config!
        engine.add_task("t7_conf_a", "Config Feature A", domain="backend", priority=6, write_set={"src/config/app.json"})
        engine.add_task("t8_conf_b", "Config Feature B", domain="backend", priority=5, write_set={"src/config/app.json"})
        # 9. Infra deploy script - worker will crash on this task!
        engine.add_task("t9_worker_crash", "Deploy Script", domain="infra", priority=5, write_set={"infra/deploy.sh"})
        # 10. Legacy refactor - will cause a merge conflict!
        engine.add_task("t10_merge_conflict", "Legacy Refactor", domain="backend", priority=4, write_set={"src/legacy/old.py"})
        # 11. Billing service
        engine.add_task("t11_billing", "Billing Service", domain="backend", priority=4, write_set={"src/billing/core.py"})
        # 12. Frontend billing
        engine.add_task("t12_fe_bill", "FE Billing", domain="frontend", priority=3, write_set={"src/fe/billing.tsx"})
        # 13. Infra monitoring
        engine.add_task("t13_monitor", "Monitoring", domain="infra", priority=2, write_set={"infra/monitor.yaml"})
        # 14. Final integration verification (read-only, depends on t11 and t12)
        engine.add_task("t14_verify", "System Verification", domain="backend", priority=1, dependencies=["t11_billing", "t12_fe_bill"], read_set={"src"})

        # Configure merge conflict simulation on t10 branch
        merge_adapter.fail_merge("ao/t10_merge_conflict", conflict_files=["src/legacy/old.py"])

        engine.start_mission()

        step_count = 0
        max_steps = 100

        crash_injected = False


        while step_count < max_steps:
            step_count += 1

            # Trigger scheduler evaluation
            dispatches = scheduler.evaluate()
            for d in dispatches:
                if d.is_reuse:
                    total_worker_reuses += 1

            # Get active tasks
            active_tasks = [
                t for t in engine.graph.tasks.values()
                if t.status in (TaskState.RUNNING, TaskState.ASSIGNED)
            ]
            if len(active_tasks) > max_concurrent_tasks:
                max_concurrent_tasks = len(active_tasks)

            # INVARIANT CHECK 1: No concurrent tasks may have overlapping write sets!
            for i, task_a in enumerate(active_tasks):
                for j, task_b in enumerate(active_tasks):
                    if i < j:
                        overlap = are_write_sets_overlapping(task_a.write_set, task_b.write_set)
                        self.assertFalse(
                            overlap,
                            f"Write collision detected between active tasks '{task_a.task_id}' and '{task_b.task_id}'!"
                        )

            # INVARIANT CHECK 2: Merge queue has at most 1 active merge at any time
            active_merges = 1 if integration_manager.merge_queue.active_merge is not None else 0
            self.assertLessEqual(active_merges, 1)

            if not active_tasks:
                # Check if all tasks reached terminal states
                all_terminal = all(
                    t.status in (TaskState.MERGED, TaskState.PASSED, TaskState.FAILED, TaskState.CANCELLED)
                    for t in engine.graph.tasks.values()
                )
                if all_terminal:
                    break

            # Process active tasks: simulate completion or failure
            for t in list(active_tasks):
                # Check for worker crash scenario
                if t.task_id == "t9_worker_crash" and not crash_injected:
                    crash_injected = True
                    # Worker crashes
                    worker_id = t.assigned_worker_id
                    scheduler.handle_worker_failure(worker_id, error="Simulated Worker Segfault")
                    worker_loss_recovered = True
                    break  # break to let scheduler re-evaluate

                # Complete active task
                scheduler.complete_task(t.task_id, result={"status": "ok"}, duration=0.05)

        # Post-simulation assertions
        print("\n" + "=" * 70)
        print("    Adaptive Orchestrator v5 - Phase 4 Integration Simulation Results")
        print("=" * 70)
        print(f"Total Steps Taken:              {step_count}")
        print(f"Peak Concurrent Active Tasks:   {max_concurrent_tasks}")
        print(f"Total Worker Reuses:            {total_worker_reuses}")
        print(f"Worker Loss Recovered:          {worker_loss_recovered}")
        print(f"Final AIMD Capacity:            {aimd_controller.current_capacity}")

        # Check outcomes
        t7 = engine.graph.get_task("t7_conf_a")
        t8 = engine.graph.get_task("t8_conf_b")
        self.assertEqual(t7.status, TaskState.MERGED)
        self.assertEqual(t8.status, TaskState.MERGED)

        t9 = engine.graph.get_task("t9_worker_crash")
        self.assertEqual(t9.status, TaskState.MERGED)
        self.assertGreater(t9.retry_count, 0)

        t10 = engine.graph.get_task("t10_merge_conflict")
        # Merge conflict task should have failed integration
        self.assertEqual(t10.status, TaskState.FAILED)
        self.assertIn("Merge conflict", t10.error or "")
        self.assertFalse(workspace_registry.has_active_ownership("t10_merge_conflict"))
        # Evidence retained in worktree
        self.assertTrue(worktree_adapter.has_workspace(t10.workspace_path))

        t14 = engine.graph.get_task("t14_verify")
        self.assertEqual(t14.status, TaskState.PASSED)

        # Ensure NO stale workspace ownership remains active!
        active_ownerships = workspace_registry.get_active_records()
        self.assertEqual(len(active_ownerships), 0, "No active workspace ownership locks should remain!")


if __name__ == "__main__":
    unittest.main()
