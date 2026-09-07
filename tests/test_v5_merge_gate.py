"""
Unit and Integration tests for Evidence-Gated Merge Queue.
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
from orchestrator.verification.policy import VerificationPolicy
from orchestrator.verification.verifier import (
    MockIndependentVerifier,
    Tier1SelfTestVerifier,
)
from orchestrator.workers.adapter import MockExecutionAdapter
from orchestrator.workers.models import Worker
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workspace.adapter import MockWorktreeAdapter
from orchestrator.workspace.models import WorkspaceMode
from orchestrator.workspace.registry import WorkspaceRegistry


class TestMergeGate(unittest.TestCase):
    def setUp(self):
        self.ready_queue = ReadyQueue()
        self.worker_registry = WorkerRegistry()
        self.workspace_registry = WorkspaceRegistry()
        self.worktree_adapter = MockWorktreeAdapter()
        self.merge_adapter = MockMergeAdapter()
        self.integration_manager = IntegrationManager(
            merge_adapter=self.merge_adapter,
            workspace_registry=self.workspace_registry,
            worktree_adapter=self.worktree_adapter,
        )
        self.exec_adapter = MockExecutionAdapter(default_success=True)
        self.events: list[Event] = []

        self.t1_verifier = Tier1SelfTestVerifier(default_success=True)
        self.t2_verifier = MockIndependentVerifier(default_success=True)
        self.verif_engine = VerificationEngine(
            tier1_verifier=self.t1_verifier,
            independent_verifier=self.t2_verifier,
            event_emitter=self._emit_event,
        )

        self.scheduler = EventDrivenScheduler(
            ready_queue=self.ready_queue,
            worker_registry=self.worker_registry,
            workspace_registry=self.workspace_registry,
            worktree_adapter=self.worktree_adapter,
            integration_manager=self.integration_manager,
            execution_adapter=self.exec_adapter,
            verification_engine=self.verif_engine,
            event_emitter=self._emit_event,
        )

    def _emit_event(self, event_type, task_id=None, payload=None):
        ev = Event(event_type=event_type, mission_id="m_gate", task_id=task_id, payload=payload or {})
        self.events.append(ev)
        return ev

    def test_successful_verification_gates_merge_ready_and_merges(self):
        """
        Task completed -> verification passes -> MERGE_READY emitted -> merge completes -> MERGED.
        """
        worker = Worker(worker_id="w1", domain="backend")
        self.worker_registry.register_worker(worker)

        task = Task(
            task_id="t_merge_pass",
            mission_id="m_gate",
            title="Valid Feature",
            domain="backend",
            workspace_mode=WorkspaceMode.BRANCH.value,
            write_set={"src/feature.py"},
            branch_name="ao/t_merge_pass",
            status=TaskState.READY,
        )
        self.ready_queue.push(task)

        # Run scheduler
        self.scheduler.evaluate()

        # Task should have verified and cleanly integrated into MERGED
        self.assertEqual(task.status, TaskState.MERGED)
        ev_types = [e.event_type for e in self.events]
        self.assertIn(EventType.VERIFICATION_STARTED, ev_types)
        self.assertIn(EventType.VERIFICATION_PASSED, ev_types)
        self.assertIn(EventType.MERGE_READY, ev_types)
        self.assertIn(EventType.MERGE_COMPLETED, ev_types)

    def test_failed_verification_blocks_merge_queue(self):
        """
        If verification fails, task must NOT become MERGE_READY and must NOT enter MergeQueue.
        """
        worker = Worker(worker_id="w2", domain="backend")
        self.worker_registry.register_worker(worker)

        task = Task(
            task_id="t_merge_fail",
            mission_id="m_gate",
            title="Broken Feature",
            domain="backend",
            workspace_mode=WorkspaceMode.BRANCH.value,
            write_set={"src/bad_feature.py"},
            branch_name="ao/t_merge_fail",
            status=TaskState.READY,
            max_retries=0,  # No retries to immediately observe gate blocking
        )
        self.ready_queue.push(task)

        # Verifier fails
        res_fail = VerificationResult(
            task_id="t_merge_fail",
            passed=False,
            tier=VerificationTier.TIER_1_SELF_TEST,
            failure_classification=FailureClassification.REPAIRABLE,
            summary="Syntax error in feature",
        )
        self.t1_verifier.set_task_override("t_merge_fail", res_fail)

        self.scheduler.evaluate()

        # Must be FAILED, NOT MERGED, NOT MERGE_READY
        self.assertEqual(task.status, TaskState.FAILED)
        ev_types = [e.event_type for e in self.events]
        self.assertIn(EventType.VERIFICATION_FAILED, ev_types)
        self.assertNotIn(EventType.MERGE_READY, ev_types)
        self.assertNotIn(EventType.MERGE_STARTED, ev_types)
        self.assertNotIn(EventType.MERGE_COMPLETED, ev_types)

        # Merge queue must be empty
        self.assertEqual(len(self.integration_manager.queue.get_pending_requests()), 0)
        self.assertEqual(len(self.integration_manager.queue.get_history()), 0)

    def test_tier2_required_task_cannot_bypass_independent_verification(self):
        """
        If policy requires Tier 2 (e.g. high risk), task cannot merge even if Tier 1 passed,
        unless Tier 2 also passes.
        """
        worker = Worker(worker_id="w3", domain="security")
        self.worker_registry.register_worker(worker)

        task = Task(
            task_id="t_high_risk",
            mission_id="m_gate",
            title="Auth Refactor",
            domain="security",
            workspace_mode=WorkspaceMode.BRANCH.value,
            write_set={"auth/core.py"},
            branch_name="ao/auth_refactor",
            status=TaskState.READY,
            metadata={
                "risk": "high",  # Automatically enforces Tier 2 requirement
                "require_tier2": True,
            },
            max_retries=0,
        )
        self.ready_queue.push(task)

        # Tier 1 passes
        # Tier 2 fails!
        self.t2_verifier.set_task_override(
            "t_high_risk",
            success=False,
            error_message="Security audit detected missing CSRF token",
            classification=FailureClassification.REPAIRABLE,
        )

        self.scheduler.evaluate()

        # Task must be blocked from merging
        self.assertEqual(task.status, TaskState.FAILED)
        ev_types = [e.event_type for e in self.events]
        self.assertIn(EventType.VERIFICATION_STARTED, ev_types)
        self.assertIn(EventType.VERIFICATION_FAILED, ev_types)
        self.assertNotIn(EventType.MERGE_READY, ev_types)

    def test_tier1_only_task_merges_without_tier2(self):
        """
        Routine task with allow_merge_after_tier1=True merges after Tier 1 passes
        without needing Tier 2.
        """
        worker = Worker(worker_id="w4", domain="frontend")
        self.worker_registry.register_worker(worker)

        task = Task(
            task_id="t_routine",
            mission_id="m_gate",
            title="CSS Update",
            domain="frontend",
            workspace_mode=WorkspaceMode.BRANCH.value,
            write_set={"styles.css"},
            branch_name="ao/css_update",
            status=TaskState.READY,
            metadata={"risk": "low"},
        )
        self.ready_queue.push(task)

        # Ensure Tier 2 verifier was NOT even invoked
        initial_t2_invocations = len(self.t2_verifier.invocations)
        self.scheduler.evaluate()

        self.assertEqual(task.status, TaskState.MERGED)
        # Tier 2 was not called because policy allows merge after Tier 1
        self.assertEqual(len(self.t2_verifier.invocations), initial_t2_invocations)


if __name__ == "__main__":
    unittest.main()
