"""
Unit tests for Verification Models, Evidence Serialization, and Truncation.
Adaptive Orchestrator v5 - Phase 5.
"""

import unittest
import time
from orchestrator.verification.models import (
    FailureClassification,
    VerificationEvidence,
    VerificationResult,
    VerificationStatus,
    VerificationTier,
    truncate_summary,
)


class TestVerificationModels(unittest.TestCase):
    def test_evidence_creation_and_defaults(self):
        ev = VerificationEvidence(
            task_id="t1",
            tier=VerificationTier.TIER_1_SELF_TEST,
            status=VerificationStatus.PASSED,
            command="pytest tests/unit",
            exit_code=0,
            stdout_summary="10 passed in 0.2s",
            verifier_identity="worker_self_test",
        )
        self.assertEqual(ev.task_id, "t1")
        self.assertEqual(ev.tier, VerificationTier.TIER_1_SELF_TEST)
        self.assertTrue(ev.is_success)
        self.assertEqual(ev.exit_code, 0)
        self.assertTrue(ev.verification_id.startswith("verif_"))

    def test_evidence_serialization(self):
        ev = VerificationEvidence(
            task_id="task_api",
            tier=VerificationTier.TIER_2_INDEPENDENT,
            status=VerificationStatus.FAILED,
            command="flake8 .",
            exit_code=1,
            stdout_summary="",
            stderr_summary="F401 'os' imported but unused",
            failure_classification=FailureClassification.REPAIRABLE,
            verifier_identity="independent_verifier",
            details={"rule": "pep8"},
        )
        data = ev.to_dict()
        self.assertEqual(data["task_id"], "task_api")
        self.assertEqual(data["tier"], "TIER_2_INDEPENDENT")
        self.assertEqual(data["status"], "FAILED")
        self.assertEqual(data["failure_classification"], "REPAIRABLE")
        self.assertEqual(data["details"]["rule"], "pep8")
        self.assertIn("F401", data["stderr_summary"])

    def test_log_truncation_enforces_compactness(self):
        huge_log = "ERROR: " + ("x" * 2000) + " END_OF_LOG"
        ev = VerificationEvidence(
            task_id="t_heavy",
            stderr_summary=huge_log,
        )
        # Should be truncated to <= 500 chars (plus truncation notice)
        self.assertLess(len(ev.stderr_summary), 600)
        self.assertIn("... [truncated] ...", ev.stderr_summary)
        self.assertTrue(ev.stderr_summary.startswith("ERROR: x"))
        self.assertTrue(ev.stderr_summary.endswith("END_OF_LOG"))

    def test_verification_result_aggregation(self):
        ev1 = VerificationEvidence(
            task_id="t_core",
            tier=VerificationTier.TIER_1_SELF_TEST,
            status=VerificationStatus.PASSED,
        )
        ev2 = VerificationEvidence(
            task_id="t_core",
            tier=VerificationTier.TIER_2_INDEPENDENT,
            status=VerificationStatus.PASSED,
        )
        result = VerificationResult(
            task_id="t_core",
            passed=True,
            tier=VerificationTier.TIER_2_INDEPENDENT,
            evidences=[ev1, ev2],
            summary="All tests passed",
        )
        self.assertTrue(result.passed)
        self.assertTrue(result.is_success)
        self.assertEqual(len(result.evidences), 2)
        d = result.to_dict()
        self.assertEqual(d["passed"], True)
        self.assertEqual(len(d["evidences"]), 2)
        self.assertIsNone(d["failure_classification"])

    def test_failure_classification_is_repairable_property(self):
        self.assertTrue(FailureClassification.REPAIRABLE.is_repairable)
        self.assertFalse(FailureClassification.NON_REPAIRABLE.is_repairable)
        self.assertFalse(FailureClassification.ENVIRONMENTAL.is_repairable)
        self.assertFalse(FailureClassification.INFRASTRUCTURE.is_repairable)
        self.assertFalse(FailureClassification.UNKNOWN.is_repairable)


if __name__ == "__main__":
    unittest.main()
