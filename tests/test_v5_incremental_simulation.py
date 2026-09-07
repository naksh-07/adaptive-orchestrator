"""
Integration and Simulation tests for Incremental Verification, In-Context Repair, and Parallel Non-Blocking Execution.
Adaptive Orchestrator v5 - Phase 5.
"""

import unittest
from orchestrator.engine import MissionEngine
from orchestrator.integration.adapter import MockMergeAdapter
from orchestrator.integration.manager import IntegrationManager
from orchestrator.models import Event, EventType, Task, TaskState
from orchestrator.scheduler.ready_queue import ReadyQueue
from orchestrator.scheduler.scheduler import EventDrivenScheduler
from orchestrator.verification.engine import VerificationEngine
from orchestrator.verification.models import (
    FailureClassification,
    VerificationEvidence,
    VerificationResult,
    VerificationStatus,
    VerificationTier,
)
from orchestrator.verification.verifier import (
    MockIndependentVerifier,
    Tier1SelfTestVerifier,
)
from orchestrator.workers.adapter import ExecutionResult, MockExecutionAdapter
from orchestrator.workers.models import Worker
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workspace.adapter import MockWorktreeAdapter
from orchestrator.workspace.models import WorkspaceMode
from orchestrator.workspace.registry import WorkspaceRegistry


class TestIncrementalSimulation(unittest.TestCase):
    def test_incremental_parallelism_no_global_barrier(self):
        """
        Prove that verification is incremental and task-local:
        Task A finishes implementation and enters VERIFYING.
        Task B remains RUNNING simultaneously.
        Task C executes independently.
        No global mission-level barrier blocks Task B or C while Task A verifies.
        """
        engine = MissionEngine(mission_id="m_sim", title="Incremental Parallelism")
        ready_queue = engine.ready_queue
        worker_registry = engine.workers
        workspace_registry = engine.workspace_registry
        worktree_adapter = MockWorktreeAdapter()
        merge_adapter = MockMergeAdapter()
        integration_manager = IntegrationManager(
            merge_adapter=merge_adapter,
            workspace_registry=workspace_registry,
            worktree_adapter=worktree_adapter,
        )

        # Manual completion adapter so we can freeze execution states
        exec_adapter = MockExecutionAdapter(auto_complete=False)
        t1_verifier = Tier1SelfTestVerifier(default_success=True)
        t2_verifier = MockIndependentVerifier(default_success=True)
        verif_engine = VerificationEngine(
            tier1_verifier=t1_verifier,
            independent_verifier=t2_verifier,
            event_emitter=engine.emit_event,
        )

        scheduler = EventDrivenScheduler(
            ready_queue=ready_queue,
            worker_registry=worker_registry,
            workspace_registry=workspace_registry,
            worktree_adapter=worktree_adapter,
            integration_manager=integration_manager,
            execution_adapter=exec_adapter,
            verification_engine=verif_engine,
            engine=engine,
        )
        engine.attach_scheduler(scheduler)
        engine.attach_integration_manager(integration_manager)
        engine.attach_verification_engine(verif_engine)

        # Register 3 workers in different domains
        worker_registry.register_worker(Worker(worker_id="w_a", domain="backend"))
        worker_registry.register_worker(Worker(worker_id="w_b", domain="frontend"))
        worker_registry.register_worker(Worker(worker_id="w_c", domain="devops"))

        # Add 3 independent tasks
        task_a = engine.add_task(task_id="task_a", title="Backend API", domain="backend", write_set={"api.py"})
        task_b = engine.add_task(task_id="task_b", title="Frontend UI", domain="frontend", write_set={"app.tsx"})
        task_c = engine.add_task(task_id="task_c", title="CI Pipeline", domain="devops", write_set={"ci.yml"})

        engine.start_mission()

        # All 3 tasks are dispatched and RUNNING in parallel
        self.assertEqual(task_a.status, TaskState.RUNNING)
        self.assertEqual(task_b.status, TaskState.RUNNING)
        self.assertEqual(task_c.status, TaskState.RUNNING)

        # Task A completes execution: enters verification and completes merge
        scheduler.complete_task(task_id="task_a", result={"status": "api_done"})

        # Task A is now MERGED
        self.assertEqual(task_a.status, TaskState.MERGED)

        # CRITICAL ASSERTION: Task B and Task C are STILL RUNNING! No global wave barrier stopped them!
        self.assertEqual(task_b.status, TaskState.RUNNING)
        self.assertEqual(task_c.status, TaskState.RUNNING)

        # Now Task B completes
        scheduler.complete_task(task_id="task_b", result={"status": "ui_done"})
        self.assertEqual(task_b.status, TaskState.MERGED)
        self.assertEqual(task_c.status, TaskState.RUNNING)

        # Finally Task C completes
        scheduler.complete_task(task_id="task_c", result={"status": "ci_done"})
        self.assertEqual(task_c.status, TaskState.MERGED)

    def test_end_to_end_local_repair_and_merge_simulation(self):
        """
        Synthetic end-to-end scenario:
        1. Worker completes implementation.
        2. Tier 1 fails (REPAIRABLE defect).
        3. Same worker receives targeted repair payload in its existing worktree.
        4. Worker repairs code.
        5. Tier 1 passes.
        6. Tier 2 passes.
        7. Task becomes MERGE_READY.
        8. Sequential merge queue integrates it into MERGED.
        """
        engine = MissionEngine(mission_id="m_repair_sim", title="Repair Simulation")
        worker_registry = engine.workers
        workspace_registry = engine.workspace_registry
        worktree_adapter = MockWorktreeAdapter()
        merge_adapter = MockMergeAdapter()
        integration_manager = IntegrationManager(
            merge_adapter=merge_adapter,
            workspace_registry=workspace_registry,
            worktree_adapter=worktree_adapter,
        )

        exec_adapter = MockExecutionAdapter(default_success=True)
        t1_verifier = Tier1SelfTestVerifier()
        t2_verifier = MockIndependentVerifier(default_success=True)
        verif_engine = VerificationEngine(
            tier1_verifier=t1_verifier,
            independent_verifier=t2_verifier,
            event_emitter=engine.emit_event,
        )

        scheduler = EventDrivenScheduler(
            ready_queue=engine.ready_queue,
            worker_registry=worker_registry,
            workspace_registry=workspace_registry,
            worktree_adapter=worktree_adapter,
            integration_manager=integration_manager,
            execution_adapter=exec_adapter,
            verification_engine=verif_engine,
            engine=engine,
        )
        engine.attach_scheduler(scheduler)
        engine.attach_integration_manager(integration_manager)
        engine.attach_verification_engine(verif_engine)

        worker = Worker(worker_id="w_specialist", domain="core")
        worker_registry.register_worker(worker)

        task = engine.add_task(
            task_id="task_feature",
            title="Complex Feature",
            domain="core",
            workspace_mode="branch",
            write_set={"core/feature.py"},
            metadata={"require_tier2": True},  # Enforces both Tier 1 and Tier 2
        )

        # Set initial Tier 1 failure
        fail_ev = VerificationEvidence(
            task_id="task_feature",
            tier=VerificationTier.TIER_1_SELF_TEST,
            status=VerificationStatus.FAILED,
            command="pytest tests/test_feature.py",
            exit_code=1,
            stderr_summary="AssertionError: assert feature.compute() == 100 (got 99)",
            failure_classification=FailureClassification.REPAIRABLE,
        )
        t1_verifier.set_task_override(
            "task_feature",
            VerificationResult(
                task_id="task_feature",
                passed=False,
                tier=VerificationTier.TIER_1_SELF_TEST,
                evidences=[fail_ev],
                failure_classification=FailureClassification.REPAIRABLE,
                summary="Computation test failed",
            )
        )

        # Hook repair handler: when worker repairs, clear override so Tier 1 passes
        repair_called = []
        def handle_repair(w, t, p):
            repair_called.append((w.worker_id, t.task_id, p))
            t1_verifier.task_overrides.pop("task_feature", None)
            return ExecutionResult(success=True, result={"fixed": True}, is_reuse=True)

        exec_adapter.request_repair = handle_repair

        # Start mission
        engine.start_mission()

        # Confirm repair occurred with the SAME worker
        self.assertEqual(len(repair_called), 1)
        repaired_worker_id, repaired_task_id, payload = repair_called[0]
        self.assertEqual(repaired_worker_id, "w_specialist")
        self.assertEqual(repaired_task_id, "task_feature")
        self.assertEqual(payload.failure_classification, FailureClassification.REPAIRABLE)
        self.assertIn("AssertionError", payload.error_summary)
        self.assertEqual(payload.affected_files, ["core/feature.py"])

        # Confirm task transitioned all the way to MERGED
        self.assertEqual(task.status, TaskState.MERGED)
        self.assertEqual(task.retry_count, 1)

        # Confirm verifier history retained evidence of both attempts
        hist = verif_engine.get_task_history("task_feature")
        self.assertEqual(len(hist), 2)
        self.assertFalse(hist[0].passed)
        self.assertTrue(hist[1].passed)
        self.assertEqual(hist[1].tier, VerificationTier.TIER_2_INDEPENDENT)

    def test_repeated_failure_retry_exhaustion_blocks_dependents(self):
        """
        Synthetic scenario where verification repeatedly fails:
        1. Task A fails verification.
        2. Repair attempt fails.
        3. Retry limit is exhausted.
        4. Task A becomes FAILED.
        5. Dependent Task B transitions to BLOCKED.
        """
        engine = MissionEngine(mission_id="m_exhaust_sim", title="Retry Exhaustion Simulation")
        worker_registry = engine.workers
        workspace_registry = engine.workspace_registry
        worktree_adapter = MockWorktreeAdapter()
        merge_adapter = MockMergeAdapter()
        integration_manager = IntegrationManager(
            merge_adapter=merge_adapter,
            workspace_registry=workspace_registry,
            worktree_adapter=worktree_adapter,
        )

        exec_adapter = MockExecutionAdapter(default_success=True)
        t1_verifier = Tier1SelfTestVerifier()
        verif_engine = VerificationEngine(
            tier1_verifier=t1_verifier,
            event_emitter=engine.emit_event,
        )

        scheduler = EventDrivenScheduler(
            ready_queue=engine.ready_queue,
            worker_registry=worker_registry,
            workspace_registry=workspace_registry,
            worktree_adapter=worktree_adapter,
            integration_manager=integration_manager,
            execution_adapter=exec_adapter,
            verification_engine=verif_engine,
            engine=engine,
        )
        engine.attach_scheduler(scheduler)
        engine.attach_integration_manager(integration_manager)
        engine.attach_verification_engine(verif_engine)

        worker_registry.register_worker(Worker(worker_id="w_hard", domain="core"))

        # Task A has max_retries = 1
        task_a = engine.add_task(task_id="task_a", title="Critical Base", domain="core", write_set={"base.py"})
        task_a.max_retries = 1

        # Task B depends on Task A
        task_b = engine.add_task(task_id="task_b", title="Dependent Module", domain="core", dependencies=["task_a"])

        # Permanent verification failure on Task A
        res_fail = VerificationResult(
            task_id="task_a",
            passed=False,
            tier=VerificationTier.TIER_1_SELF_TEST,
            failure_classification=FailureClassification.REPAIRABLE,
            summary="Persistent failure",
        )
        t1_verifier.set_task_override("task_a", res_fail)

        # Worker attempts repair, but verification continues to fail
        exec_adapter.request_repair = lambda w, t, p: ExecutionResult(success=True, result={}, is_reuse=True)

        engine.start_mission()

        # Task A exhausted retries and transitioned to FAILED
        self.assertEqual(task_a.status, TaskState.FAILED)
        self.assertEqual(task_a.retry_count, 1)

        # Task B must be safely BLOCKED, not enqueued or executed!
        self.assertEqual(task_b.status, TaskState.BLOCKED)
        self.assertFalse(engine.ready_queue.contains("task_b"))

        # Mission state is NOT COMPLETED
        self.assertNotEqual(engine.mission.state, "COMPLETED")


if __name__ == "__main__":
    unittest.main()
