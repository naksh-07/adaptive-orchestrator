"""
Tests for Adaptive Orchestrator v5 - Verification Pyramid Adversarial Suite.
Adversarial stress-testing covering all 8 Tier 3 conditions and all 8 Tier 4 conditions:

Tier 3 Adversarial Challenge:
  1. Undeclared write detection (write set contract breach).
  2. Failing adversarial command execution.
  3. Failing custom checker callback.
  4. Repairable failure classification (e.g. syntax, lint, unit test).
  5. Non-repairable failure classification (e.g. fatal security breach, external resource exhaustion).
  6. Task-specific explicit override behavior.
  7. Policy-disabled behavior (skipped safely without blocking execution).
  8. Verification evidence correctness and payload completeness.

Tier 4 Mission Victory Audit:
  1. All tasks completed in valid terminal states (PASSED / MERGED).
  2. Unresolved failed task rejection.
  3. Missing required deliverable artifact rejection.
  4. Acceptance criterion failure rejection.
  5. Missing verification evidence rejection (cannot claim victory on empty proof).
  6. Incomplete merge state rejection (unmerged branch worktree deliverables).
  7. Successful final audit acceptance.
  8. Repeated audit idempotency.
"""

import os
import shutil
import tempfile
import unittest

from orchestrator.engine import MissionEngine
from orchestrator.models import MissionState, Task, TaskState
from orchestrator.verification import (
    FailureClassification,
    Tier3AdversarialVerifier,
    Tier4VictoryAuditVerifier,
    VerificationEvidence,
    VerificationPolicy,
    VerificationResult,
    VerificationStatus,
    VerificationTier,
    VictoryAuditResult,
)


class TestTier3AdversarialPyramid(unittest.TestCase):
    def setUp(self):
        self.task = Task(
            id="task-sec",
            description="Payment gateway integration",
            domain="backend",
            write_set={"src/payments/gateway.py", "src/payments/config.json"},
        )

    def test_1_undeclared_write_detection(self):
        """Tier 3: Rejects undeclared file modifications outside declared write set."""
        verifier = Tier3AdversarialVerifier()
        bad_execution_result = {
            "status": "success",
            "modified_files": [
                "src/payments/gateway.py",
                "src/auth/bypass.py",  # Undeclared write!
            ]
        }
        policy = VerificationPolicy(tier3_enabled=True)
        result = verifier.verify(self.task, policy=policy, execution_result=bad_execution_result)

        self.assertFalse(result.passed)
        self.assertEqual(result.tier, VerificationTier.TIER_3_ADVERSARIAL)
        self.assertEqual(result.failure_classification, FailureClassification.WORKSPACE_COLLISION)
        self.assertTrue(any("src/auth/bypass.py" in ev.summary for ev in result.evidences))

    def test_2_failing_adversarial_command(self):
        """Tier 3: Fails when adversarial test command exits non-zero."""
        def mock_cmd_runner(cmd, cwd=None, timeout=30.0):
            if "fuzz" in cmd:
                return (1, "", "SIGSEGV: Null pointer dereference in payload parser")
            return (0, "OK", "")

        verifier = Tier3AdversarialVerifier(command_runner=mock_cmd_runner)
        self.task.metadata["tier3_command"] = "pytest tests/fuzz_payments.py"
        policy = VerificationPolicy(tier3_enabled=True)
        result = verifier.verify(self.task, policy=policy)

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_classification, FailureClassification.REPAIRABLE)
        self.assertTrue(any("SIGSEGV" in ev.stderr_summary for ev in result.evidences))

    def test_3_failing_custom_checker(self):
        """Tier 3: Fails when custom checker callback returns failure."""
        def mock_checker(task, context):
            return (False, "Custom invariant violated: transaction ID lacks UUIDv4 format", {"field": "tx_id"})

        verifier = Tier3AdversarialVerifier(checker=mock_checker)
        policy = VerificationPolicy(tier3_enabled=True)
        result = verifier.verify(self.task, policy=policy)

        self.assertFalse(result.passed)
        self.assertIn("Custom invariant violated", result.summary)

    def test_4_repairable_failure_classification(self):
        """Tier 3: Command failures are classified as repairable."""
        def mock_cmd_runner(cmd, cwd=None, timeout=30.0):
            return (1, "", "AssertionError: expected 200 OK but got 400 Bad Request")

        verifier = Tier3AdversarialVerifier(command_runner=mock_cmd_runner)
        self.task.metadata["tier3_command"] = "pytest tests/api_test.py"
        policy = VerificationPolicy(tier3_enabled=True)
        result = verifier.verify(self.task, policy=policy)

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_classification, FailureClassification.REPAIRABLE)
        self.assertTrue(result.failure_classification.is_repairable)

    def test_5_non_repairable_failure_classification(self):
        """Tier 3: Non-repairable failure classification test."""
        verifier = Tier3AdversarialVerifier()
        # Explicitly set override with NON_REPAIRABLE failure
        ev = VerificationEvidence(
            task_id="task-sec",
            tier=VerificationTier.TIER_3_ADVERSARIAL,
            status=VerificationStatus.FAILED,
            stderr_summary="Fatal unrecoverable infrastructure fault",
            failure_classification=FailureClassification.NON_REPAIRABLE,
        )
        override = VerificationResult(
            task_id="task-sec",
            passed=False,
            tier=VerificationTier.TIER_3_ADVERSARIAL,
            evidences=[ev],
            failure_classification=FailureClassification.NON_REPAIRABLE,
            summary="Fatal unrecoverable infrastructure fault",
        )
        verifier.set_task_override("task-sec", override)
        policy = VerificationPolicy(tier3_enabled=True)
        result = verifier.verify(self.task, policy=policy)

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_classification, FailureClassification.NON_REPAIRABLE)
        self.assertFalse(result.failure_classification.is_repairable)

    def test_6_task_override_behavior(self):
        """Tier 3: Respects explicit task override results."""
        verifier = Tier3AdversarialVerifier()
        override_ev = VerificationEvidence(
            task_id="task-sec",
            tier=VerificationTier.TIER_3_ADVERSARIAL,
            status=VerificationStatus.FAILED,
            verifier_identity="custom_override",
            stderr_summary="Forced manual audit failure",
        )
        override_res = VerificationResult(
            task_id="task-sec",
            passed=False,
            tier=VerificationTier.TIER_3_ADVERSARIAL,
            evidences=[override_ev],
            summary="Manual override rejection",
        )
        verifier.set_task_override("task-sec", override_res)

        policy = VerificationPolicy(tier3_enabled=True)
        result = verifier.verify(self.task, policy=policy)
        self.assertFalse(result.passed)
        self.assertEqual(result.summary, "Manual override rejection")

    def test_7_policy_disabled_behavior(self):
        """Tier 3: When disabled by policy, skips safely and marks passed."""
        verifier = Tier3AdversarialVerifier()
        policy = VerificationPolicy(
            tier3_enabled=False,
            require_adversarial_challenge=False,
        )
        self.task.metadata["require_tier3"] = False
        result = verifier.verify(self.task, policy=policy)

        self.assertTrue(result.passed)
        self.assertEqual(result.evidences[0].status, VerificationStatus.SKIPPED)

    def test_8_evidence_correctness(self):
        """Tier 3: Evidence records contains proper timestamps, identities, and details."""
        verifier = Tier3AdversarialVerifier(verifier_identity="adv_sentinel_v5")
        policy = VerificationPolicy(tier3_enabled=True)
        result = verifier.verify(self.task, policy=policy, execution_result={"modified_files": list(self.task.write_set)})

        self.assertTrue(result.passed)
        self.assertTrue(len(result.evidences) > 0)
        ev = result.evidences[0]
        self.assertEqual(ev.verifier_identity, "adv_sentinel_v5")
        self.assertEqual(ev.task_id, "task-sec")
        self.assertEqual(ev.tier, VerificationTier.TIER_3_ADVERSARIAL)
        self.assertGreater(ev.timestamp, 0.0)


class TestTier4VictoryAuditPyramid(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.engine = MissionEngine(mission_id="m_victory_pyramid", title="Victory Pyramid Test")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_1_all_tasks_valid_terminal_state(self):
        """Tier 4: Accepts when all tasks are in valid terminal states (PASSED / MERGED)."""
        t1 = Task(id="t1", domain="backend")
        t2 = Task(id="t2", domain="frontend", dependencies={"t1"})
        self.engine.add_task(t1)
        self.engine.add_task(t2)
        self.engine.start()

        self.engine.mark_task_completed("t1")
        self.engine.mark_task_completed("t2")

        auditor = Tier4VictoryAuditVerifier()
        result = auditor.audit_mission(self.engine)
        self.assertTrue(result.passed)
        self.assertEqual(len(result.unresolved_failures), 0)

    def test_2_unresolved_failed_task_rejection(self):
        """Tier 4: Rejects mission containing any unresolved failed task."""
        t1 = Task(id="t1", domain="backend")
        self.engine.add_task(t1)
        self.engine.start()
        self.engine.mark_task_failed("t1", error="Fatal DB migration error", can_retry=False)

        auditor = Tier4VictoryAuditVerifier()
        result = auditor.audit_mission(self.engine)
        self.assertFalse(result.passed)
        self.assertEqual(len(result.unresolved_failures), 1)
        self.assertTrue(any("t1" in f for f in result.unresolved_failures))

    def test_3_missing_required_artifact_rejection(self):
        """Tier 4: Rejects mission when required deliverable artifact is missing from disk."""
        t1 = Task(id="t1", domain="backend")
        self.engine.add_task(t1)
        self.engine.start()
        self.engine.mark_task_completed("t1")

        missing_path = os.path.join(self.test_dir, "non_existent_report.json")
        auditor = Tier4VictoryAuditVerifier(required_artifacts=[missing_path])
        result = auditor.audit_mission(self.engine, required_artifacts=[missing_path])

        self.assertFalse(result.passed)
        self.assertTrue(any("Missing required artifact" in f for f in result.unresolved_failures))

    def test_4_acceptance_criterion_failure_rejection(self):
        """Tier 4: Rejects mission when acceptance criteria are not verified."""
        t1 = Task(id="t1", domain="backend")
        self.engine.add_task(t1)
        self.engine.start()
        self.engine.mark_task_completed("t1")

        criteria = {"min_test_coverage": 95}
        auditor = Tier4VictoryAuditVerifier(acceptance_criteria=criteria)
        result = auditor.audit_mission(
            self.engine,
            acceptance_criteria=criteria,
            verification_history={"t1": []}  # Missing evidence for criteria!
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("missing verification evidence" in f.lower() for f in result.unresolved_failures))

    def test_5_missing_verification_evidence_rejection(self):
        """Tier 4: Rejects mission if acceptance evidence is incomplete."""
        t1 = Task(id="t1", domain="backend")
        self.engine.add_task(t1)
        self.engine.start()
        self.engine.mark_task_completed("t1")

        auditor = Tier4VictoryAuditVerifier()
        # Empty verification history passed
        result = auditor.audit_mission(
            self.engine,
            verification_history={}
        )
        self.assertFalse(result.passed)
        self.assertTrue(any("missing verification evidence" in f.lower() for f in result.unresolved_failures))

    def test_6_incomplete_merge_state_rejection(self):
        """Tier 4: Rejects mission if branch worktree deliverables were never merged."""
        t1 = Task(
            id="t1",
            domain="backend",
            workspace_mode="branch",
            write_set={"api.py"},
            branch_name="feature-api",
        )
        self.engine.add_task(t1)
        self.engine.start()
        t1.transition_to(TaskState.RUNNING)
        t1.transition_to(TaskState.VERIFYING)
        t1.transition_to(TaskState.PASSED)  # Passed but NOT merged!

        auditor = Tier4VictoryAuditVerifier()
        result = auditor.audit_mission(self.engine)
        self.assertFalse(result.passed)
        self.assertTrue(any("were not merged" in f for f in result.unresolved_failures))

    def test_7_successful_final_audit(self):
        """Tier 4: Accepts mission meeting all requirements, artifacts, and criteria."""
        t1 = Task(id="t1", domain="backend")
        self.engine.add_task(t1)
        self.engine.start()
        self.engine.mark_task_completed("t1")

        valid_artifact = os.path.join(self.test_dir, "build_manifest.json")
        with open(valid_artifact, "w", encoding="utf-8") as f:
            f.write('{"status": "built"}')

        ev = VerificationEvidence(
            task_id="t1",
            tier=VerificationTier.TIER_2_INDEPENDENT,
            status=VerificationStatus.PASSED,
        )
        v_res = VerificationResult(task_id="t1", passed=True, tier=VerificationTier.TIER_2_INDEPENDENT, evidences=[ev])

        auditor = Tier4VictoryAuditVerifier(required_artifacts=[valid_artifact])
        result = auditor.audit_mission(
            self.engine,
            required_artifacts=[valid_artifact],
            verification_history={"t1": [v_res]}
        )
        self.assertTrue(result.passed)
        self.assertEqual(len(result.unresolved_failures), 0)

    def test_8_repeated_audit_idempotency(self):
        """Tier 4: Repeated audit calls on same engine are deterministic and idempotent."""
        t1 = Task(id="t1", domain="backend")
        self.engine.add_task(t1)
        self.engine.start()
        self.engine.mark_task_completed("t1")

        auditor = Tier4VictoryAuditVerifier()
        result1 = auditor.audit_mission(self.engine)
        result2 = auditor.audit_mission(self.engine)

        self.assertEqual(result1.passed, result2.passed)
        self.assertEqual(result1.unresolved_failures, result2.unresolved_failures)


if __name__ == "__main__":
    unittest.main()
