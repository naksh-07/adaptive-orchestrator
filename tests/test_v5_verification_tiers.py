"""
Unit tests for Verification Tiers: Tier 1 Self-Test, Tier 2 Independent, and Deferred Tier Stubs.
Adaptive Orchestrator v5 - Phase 5.
"""

import unittest
from orchestrator.models import Task, TaskState
from orchestrator.verification.models import (
    FailureClassification,
    VerificationStatus,
    VerificationTier,
)
from orchestrator.verification.policy import VerificationPolicy
from orchestrator.verification.verifier import (
    MockIndependentVerifier,
    Tier1SelfTestVerifier,
    Tier3AdversarialVerifier,
    Tier4VictoryAuditVerifier,
)


class TestVerificationTiers(unittest.TestCase):
    def test_tier1_default_success_and_evidence(self):
        verifier = Tier1SelfTestVerifier(default_success=True)
        task = Task(task_id="t1", title="Task 1")
        result = verifier.verify(task)

        self.assertTrue(result.passed)
        self.assertEqual(result.tier, VerificationTier.TIER_1_SELF_TEST)
        self.assertEqual(len(result.evidences), 1)
        self.assertEqual(result.evidences[0].status, VerificationStatus.PASSED)
        self.assertEqual(result.evidences[0].verifier_identity, "tier1_worker_self_test")

    def test_tier1_command_execution_success(self):
        # Mock command runner returning (exit_code, stdout, stderr)
        mock_runner = lambda cmd, cwd, timeout: (0, "All 5 unit tests passed", "")
        verifier = Tier1SelfTestVerifier(command_runner=mock_runner)

        task = Task(task_id="t_cmd", title="Task with Command")
        policy = VerificationPolicy(
            tier1_enabled=True,
            tier1_commands=["pytest tests/unit"]
        )

        result = verifier.verify(task, policy=policy)
        self.assertTrue(result.passed)
        self.assertEqual(len(result.evidences), 1)
        ev = result.evidences[0]
        self.assertEqual(ev.exit_code, 0)
        self.assertEqual(ev.command, "pytest tests/unit")
        self.assertIn("All 5 unit tests passed", ev.stdout_summary)

    def test_tier1_command_execution_failure(self):
        mock_runner = lambda cmd, cwd, timeout: (1, "", "AssertionError: expected 42 got 0")
        verifier = Tier1SelfTestVerifier(command_runner=mock_runner)

        task = Task(task_id="t_fail", title="Task Failure")
        policy = VerificationPolicy(
            tier1_enabled=True,
            tier1_commands=["pytest tests/unit"]
        )

        result = verifier.verify(task, policy=policy)
        self.assertFalse(result.passed)
        self.assertEqual(len(result.evidences), 1)
        ev = result.evidences[0]
        self.assertEqual(ev.exit_code, 1)
        self.assertEqual(ev.status, VerificationStatus.FAILED)
        self.assertIn("AssertionError", ev.stderr_summary)
        self.assertEqual(result.failure_classification, FailureClassification.REPAIRABLE)

    def test_tier1_command_timeout(self):
        def timeout_runner(cmd, cwd, timeout):
            return (124, "", f"Command timed out after {timeout}s")

        verifier = Tier1SelfTestVerifier(command_runner=timeout_runner)
        task = Task(task_id="t_timeout", title="Timeout Task")
        policy = VerificationPolicy(
            tier1_enabled=True,
            tier1_commands=["sleep 100"],
            timeout=1.0,
        )
        result = verifier.verify(task, policy=policy)
        self.assertFalse(result.passed)
        self.assertEqual(result.evidences[0].exit_code, 124)
        self.assertIn("timed out", result.evidences[0].stderr_summary)

    def test_tier2_independent_verifier_pass_and_fail(self):
        indep_verifier = MockIndependentVerifier(default_success=True)
        task = Task(task_id="t_indep", title="Independent Task")

        # By default passes
        res1 = indep_verifier.verify_independent(task, evidence_so_far=[])
        self.assertTrue(res1.passed)
        self.assertEqual(res1.tier, VerificationTier.TIER_2_INDEPENDENT)

        # Set explicit independent failure
        indep_verifier.set_task_override(
            "t_indep",
            success=False,
            error_message="Contract violation: missing API response field",
            classification=FailureClassification.REPAIRABLE,
        )
        res2 = indep_verifier.verify_independent(task, evidence_so_far=[])
        self.assertFalse(res2.passed)
        self.assertEqual(res2.failure_classification, FailureClassification.REPAIRABLE)
        self.assertIn("Contract violation", res2.evidences[0].stderr_summary)

    def test_tier2_does_not_mirror_tier1_state(self):
        """
        Proof that Tier 2 does NOT blindly trust Tier 1 results.
        Tier 1 passes, but Tier 2 independently audits and catches a defect.
        """
        t1_verifier = Tier1SelfTestVerifier(default_success=True)
        t2_verifier = MockIndependentVerifier(default_success=True)

        task = Task(task_id="t_sneaky", title="Subtle Bug Task")

        # Tier 1 says PASSED
        t1_res = t1_verifier.verify(task)
        self.assertTrue(t1_res.passed)

        # Tier 2 independently inspects and rejects the deliverable!
        t2_verifier.set_task_override(
            "t_sneaky",
            success=False,
            error_message="Security audit failed: unescaped input parameter",
            classification=FailureClassification.REPAIRABLE,
        )

        t2_res = t2_verifier.verify_independent(task, evidence_so_far=t1_res.evidences)
        self.assertFalse(t2_res.passed)
        self.assertNotEqual(t1_res.passed, t2_res.passed)
        self.assertEqual(t2_res.evidences[0].details["tier1_evidence_count"], 1)

    def test_tier3_and_tier4_stubs_are_deferred(self):
        task = Task(task_id="t_stub", title="Stub Task")
        t3 = Tier3AdversarialVerifier()
        with self.assertRaises(NotImplementedError) as ctx3:
            t3.verify(task)
        self.assertIn("deferred", str(ctx3.exception).lower())

        t4 = Tier4VictoryAuditVerifier()
        with self.assertRaises(NotImplementedError) as ctx4:
            t4.verify(task)
        self.assertIn("deferred", str(ctx4.exception).lower())


if __name__ == "__main__":
    unittest.main()
