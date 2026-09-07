"""
Unit and Integration tests for Failure Classification and Local In-Context Repair Loop.
Adaptive Orchestrator v5 - Phase 5.
"""

import unittest
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
from orchestrator.verification.repair import (
    RepairCoordinator,
    RepairPayload,
    classify_failure,
)
from orchestrator.verification.verifier import (
    MockIndependentVerifier,
    Tier1SelfTestVerifier,
)
from orchestrator.workers.adapter import ExecutionResult, MockExecutionAdapter
from orchestrator.workers.models import Worker
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workspace.models import WorkspaceMode, WorkspaceReleaseState
from orchestrator.workspace.registry import WorkspaceRegistry


class TestRepairLoop(unittest.TestCase):
    def test_failure_classification_rules(self):
        # 1. Code defect -> REPAIRABLE
        ev_code = VerificationEvidence(
            task_id="t1",
            stderr_summary="AssertionError: expected 'success' got 'fail'",
        )
        self.assertEqual(classify_failure(ev_code), FailureClassification.REPAIRABLE)

        ev_type = VerificationEvidence(
            task_id="t1",
            stderr_summary="TypeError: unsupported operand type(s) for +: 'int' and 'str'",
        )
        self.assertEqual(classify_failure(ev_type), FailureClassification.REPAIRABLE)

        # 2. Environmental -> ENVIRONMENTAL
        ev_env = VerificationEvidence(
            task_id="t1",
            stderr_summary="ModuleNotFoundError: No module named 'cryptography'",
        )
        self.assertEqual(classify_failure(ev_env), FailureClassification.ENVIRONMENTAL)

        ev_net = VerificationEvidence(
            task_id="t1",
            stderr_summary="ConnectionRefusedError: [Errno 111] Connection refused to database",
        )
        self.assertEqual(classify_failure(ev_net), FailureClassification.ENVIRONMENTAL)

        # 3. Infrastructure -> INFRASTRUCTURE
        ev_infra = VerificationEvidence(
            task_id="t1",
            stderr_summary="Process terminated: Out of memory (OOM)",
        )
        self.assertEqual(classify_failure(ev_infra), FailureClassification.INFRASTRUCTURE)

        # 4. Unknown fallback
        ev_unk = VerificationEvidence(
            task_id="t1",
            stderr_summary="something unexpected happened with no obvious keyword",
        )
        self.assertEqual(classify_failure(ev_unk), FailureClassification.UNKNOWN)

    def test_repair_coordinator_can_repair_and_payload(self):
        coordinator = RepairCoordinator()
        task = Task(task_id="t_rep", write_set={"src/api.py", "src/models.py"}, retry_count=0, max_retries=2)

        # Repairable result
        ev = VerificationEvidence(
            task_id="t_rep",
            status=VerificationStatus.FAILED,
            command="pytest tests/unit",
            stderr_summary="AssertionError: test_login failed",
            failure_classification=FailureClassification.REPAIRABLE,
        )
        res = VerificationResult(
            task_id="t_rep",
            passed=False,
            tier=VerificationTier.TIER_1_SELF_TEST,
            evidences=[ev],
            failure_classification=FailureClassification.REPAIRABLE,
            summary="Unit test failed",
        )

        self.assertTrue(coordinator.can_repair(task, res))

        payload = coordinator.build_repair_payload(task, res)
        self.assertEqual(payload.task_id, "t_rep")
        self.assertEqual(payload.repair_attempt, 1)
        self.assertEqual(payload.failed_tier, "TIER_1_SELF_TEST")
        self.assertEqual(payload.failed_command, "pytest tests/unit")
        self.assertEqual(payload.failure_classification, FailureClassification.REPAIRABLE)
        self.assertIn("AssertionError", payload.error_summary)
        self.assertEqual(payload.affected_files, ["src/api.py", "src/models.py"])

    def test_repair_coordinator_respects_retry_limit(self):
        coordinator = RepairCoordinator()
        # Task already at max_retries
        task = Task(task_id="t_exhausted", retry_count=2, max_retries=2)
        res = VerificationResult(
            task_id="t_exhausted",
            passed=False,
            tier=VerificationTier.TIER_1_SELF_TEST,
            failure_classification=FailureClassification.REPAIRABLE,
        )
        self.assertFalse(coordinator.can_repair(task, res))

    def test_in_context_repair_reuses_same_worker_and_workspace(self):
        """
        Verify that when verification fails, the scheduler:
        1. Keeps the worker and workspace context warm.
        2. Sends focused RepairPayload to the same worker.
        3. Re-runs verification after repair succeeds.
        """
        ready_queue = ReadyQueue()
        worker_registry = WorkerRegistry()
        workspace_registry = WorkspaceRegistry()
        adapter = MockExecutionAdapter(default_success=True)

        worker = Worker(worker_id="w_backend_1", domain="backend")
        worker_registry.register_worker(worker)

        events: list[Event] = []
        def emitter(event_type, task_id=None, payload=None):
            ev = Event(event_type=event_type, mission_id="m1", task_id=task_id, payload=payload or {})
            events.append(ev)
            return ev

        # Configure Tier 1 verifier: fails on first try, passes on repair!
        t1_verifier = Tier1SelfTestVerifier()
        # Mock independent verifier passes
        t2_verifier = MockIndependentVerifier(default_success=True)

        verif_engine = VerificationEngine(
            tier1_verifier=t1_verifier,
            independent_verifier=t2_verifier,
            event_emitter=emitter,
        )

        scheduler = EventDrivenScheduler(
            ready_queue=ready_queue,
            worker_registry=worker_registry,
            workspace_registry=workspace_registry,
            execution_adapter=adapter,
            verification_engine=verif_engine,
            event_emitter=emitter,
        )

        task = Task(
            task_id="task_calc",
            mission_id="m1",
            title="Calculator Implementation",
            domain="backend",
            write_set={"calc.py"},
            workspace_mode="share",
            status=TaskState.READY,
            max_retries=2,
        )
        ready_queue.push(task)

        # First verification fails with REPAIRABLE error
        ev_fail = VerificationEvidence(
            task_id="task_calc",
            tier=VerificationTier.TIER_1_SELF_TEST,
            status=VerificationStatus.FAILED,
            command="pytest test_calc.py",
            exit_code=1,
            stderr_summary="AssertionError: 2 + 2 != 5",
            failure_classification=FailureClassification.REPAIRABLE,
        )
        res_fail = VerificationResult(
            task_id="task_calc",
            passed=False,
            tier=VerificationTier.TIER_1_SELF_TEST,
            evidences=[ev_fail],
            failure_classification=FailureClassification.REPAIRABLE,
            summary="test_calc failed",
        )
        t1_verifier.set_task_override("task_calc", res_fail)

        # Execution adapter is configured so that when repair is executed, we flip Tier 1 to PASS
        def on_repair(worker_arg, task_arg, payload_arg):
            # Worker successfully repaired the defect in-context
            t1_verifier.task_overrides.pop("task_calc", None)
            return ExecutionResult(success=True, result={"repaired": True}, is_reuse=True)

        adapter.request_repair = on_repair

        # Run scheduler evaluation
        dispatches = scheduler.evaluate()
        self.assertEqual(len(dispatches), 1)

        # Task should have gone:
        # RUNNING -> VERIFYING (failed) -> RETRYING -> RUNNING (repair) -> REPAIR_COMPLETED -> VERIFYING (passed) -> PASSED
        self.assertEqual(task.status, TaskState.PASSED)
        self.assertEqual(task.retry_count, 1)

        # Event stream should show repair lifecycle
        ev_types = [e.event_type for e in events]
        self.assertIn(EventType.VERIFICATION_STARTED, ev_types)
        self.assertIn(EventType.VERIFICATION_FAILED, ev_types)
        self.assertIn(EventType.REPAIR_REQUESTED, ev_types)
        self.assertIn(EventType.TASK_RETRYING, ev_types)
        self.assertIn(EventType.REPAIR_COMPLETED, ev_types)
        self.assertIn(EventType.VERIFICATION_PASSED, ev_types)

        # Confirm same worker was used
        self.assertEqual(task.assigned_worker_id, "w_backend_1")
        self.assertEqual(worker.metrics.tasks_completed, 1)

    def test_retry_exhaustion_marks_task_failed(self):
        """
        When verification repeatedly fails and exceeds max_retries,
        the task must transition to FAILED and release its workspace.
        """
        ready_queue = ReadyQueue()
        worker_registry = WorkerRegistry()
        workspace_registry = WorkspaceRegistry()
        adapter = MockExecutionAdapter(default_success=True)

        worker = Worker(worker_id="w_fail", domain="backend")
        worker_registry.register_worker(worker)

        events: list[Event] = []
        def emitter(event_type, task_id=None, payload=None):
            ev = Event(event_type=event_type, mission_id="m1", task_id=task_id, payload=payload or {})
            events.append(ev)
            return ev

        t1_verifier = Tier1SelfTestVerifier()
        verif_engine = VerificationEngine(
            tier1_verifier=t1_verifier,
            event_emitter=emitter,
        )

        scheduler = EventDrivenScheduler(
            ready_queue=ready_queue,
            worker_registry=worker_registry,
            workspace_registry=workspace_registry,
            execution_adapter=adapter,
            verification_engine=verif_engine,
            event_emitter=emitter,
        )

        task = Task(
            task_id="task_stubborn",
            mission_id="m1",
            title="Stubborn Bug",
            domain="backend",
            write_set={"bad.py"},
            workspace_mode="share",
            status=TaskState.READY,
            max_retries=1,  # Only 1 retry allowed
        )
        ready_queue.push(task)

        # Verifier permanently fails
        res_fail = VerificationResult(
            task_id="task_stubborn",
            passed=False,
            tier=VerificationTier.TIER_1_SELF_TEST,
            failure_classification=FailureClassification.REPAIRABLE,
            summary="Permanent unit test failure",
        )
        t1_verifier.set_task_override("task_stubborn", res_fail)

        # Worker attempts repair, but verifier still fails
        adapter.request_repair = lambda w, t, p: ExecutionResult(success=True, result={}, is_reuse=True)

        scheduler.evaluate()

        # Task should have exhausted its retry (retry_count == 1 >= max_retries 1) and reached FAILED
        self.assertEqual(task.status, TaskState.FAILED)
        self.assertEqual(task.retry_count, 1)

        # Workspace ownership must be safely released
        self.assertFalse(workspace_registry.has_active_ownership("task_stubborn"))
        self.assertEqual(worker_registry.busy_count, 0)


if __name__ == "__main__":
    unittest.main()
