"""
Tests for Adaptive Orchestrator v5 - Phase 6: Tier 3 Adversarial Challenge & Tier 4 Victory Audit.
"""

import os
import shutil
import tempfile
import unittest

from orchestrator.engine import MissionEngine
from orchestrator.models import MissionState, Task, TaskState
from orchestrator.verification import (
    FailureClassification,
    MockAdversarialVerifier,
    MockVictoryAuditVerifier,
    Tier3AdversarialVerifier,
    Tier4VictoryAuditVerifier,
    VerificationEngine,
    VerificationPolicy,
    VerificationStatus,
    VerificationTier,
    VictoryAuditResult,
)


class TestTier3AdversarialChallenge(unittest.TestCase):
    def setUp(self):
        self.verifier = MockAdversarialVerifier()
        self.task = Task(
            id="task_adv_1",
            description="High risk financial calculation",
            domain="backend",
            write_set={"ledger.py"},
        )

    def test_mock_adversarial_pass(self):
        result = self.verifier.verify(self.task, execution_result={"status": "ok"})
        self.assertEqual(result.tier, VerificationTier.TIER3_ADVERSARIAL)
        self.assertEqual(result.status, VerificationStatus.PASSED)
        self.assertTrue(result.passed)
        self.assertIn("Adversarial checks passed", result.evidence.summary)

    def test_mock_adversarial_failure(self):
        fail_verifier = MockAdversarialVerifier(should_pass=False, failure_reason="Found subtle rounding overflow")
        result = fail_verifier.verify(self.task, execution_result={"status": "ok"})
        self.assertEqual(result.status, VerificationStatus.FAILED)
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_classification, FailureClassification.LOGIC_ERROR)
        self.assertIn("rounding overflow", result.evidence.summary)

    def test_tier3_write_set_contract_violation_check(self):
        # Base verifier checks modified_files vs declared write_set
        verifier = Tier3AdversarialVerifier()
        exec_with_unauthorized_write = {
            "status": "ok",
            "modified_files": ["ledger.py", "unauthorized_override.py"],
        }
        result = verifier.verify(self.task, execution_result=exec_with_unauthorized_write)
        self.assertEqual(result.status, VerificationStatus.FAILED)
        self.assertEqual(result.failure_classification, FailureClassification.WORKSPACE_COLLISION)
        self.assertIn("Contract violation", result.evidence.summary)

    def test_tier3_is_non_mutating(self):
        verifier = Tier3AdversarialVerifier()
        orig_desc = self.task.description
        orig_write_set = set(self.task.write_set)
        verifier.verify(self.task, execution_result={"status": "ok", "modified_files": ["ledger.py"]})
        self.assertEqual(self.task.description, orig_desc)
        self.assertEqual(self.task.write_set, orig_write_set)


class TestTier4VictoryAudit(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.engine = MissionEngine(mission_id="m_victory", title="Victory Audit Mission")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_victory_audit_success(self):
        t1 = Task(id="t1", description="Backend Component", domain="backend")
        t2 = Task(id="t2", description="Frontend Component", domain="frontend")
        self.engine.add_task(t1)
        self.engine.add_task(t2)
        self.engine.start()

        # Complete and verify both tasks
        self.engine.mark_task_completed("t1")
        self.engine.mark_task_completed("t2")
        self.assertTrue(t1.state.is_success)
        self.assertTrue(t2.state.is_success)

        # Mission state transitioned to AUDITING (or COMPLETED if auto_audit)
        verifier = Tier4VictoryAuditVerifier()
        result = verifier.audit_mission(self.engine)
        self.assertTrue(result.passed)
        self.assertEqual(len(result.unresolved_failures), 0)

    def test_victory_audit_rejects_failed_task(self):
        t1 = Task(id="t1", description="Task 1", domain="backend")
        t2 = Task(id="t2", description="Task 2", domain="frontend")
        self.engine.add_task(t1)
        self.engine.add_task(t2)
        self.engine.start()

        self.engine.mark_task_completed("t1")
        # Fail t2
        self.engine.mark_task_failed("t2", error="Syntax error")

        verifier = Tier4VictoryAuditVerifier()
        result = verifier.audit_mission(self.engine)
        self.assertFalse(result.passed)
        self.assertIn("Task t2 in state FAILED", result.unresolved_failures)

    def test_victory_audit_rejects_missing_required_artifact(self):
        t1 = Task(id="t1", description="Task 1", domain="backend")
        self.engine.add_task(t1)
        self.engine.start()
        self.engine.mark_task_completed("t1")

        missing_artifact = os.path.join(self.test_dir, "nonexistent_report.md")
        verifier = Tier4VictoryAuditVerifier(required_artifacts=[missing_artifact])
        result = verifier.audit_mission(self.engine)
        self.assertFalse(result.passed)
        self.assertIn("Missing required artifact", result.evidence.get("artifacts", ""))

    def test_victory_audit_accepts_present_artifact(self):
        t1 = Task(id="t1", description="Task 1", domain="backend")
        self.engine.add_task(t1)
        self.engine.start()
        self.engine.mark_task_completed("t1")

        artifact = os.path.join(self.test_dir, "report.md")
        with open(artifact, "w", encoding="utf-8") as f:
            f.write("# Final Report\nSuccess.")

        verifier = Tier4VictoryAuditVerifier(required_artifacts=[artifact])
        result = verifier.audit_mission(self.engine)
        self.assertTrue(result.passed)
