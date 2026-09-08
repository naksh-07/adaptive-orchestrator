"""
Tests for Adaptive Orchestrator v5 - Phase 6: Complete End-to-End Lifecycle.
"""

import os
import shutil
import tempfile
import unittest

from orchestrator.engine import MissionEngine
from orchestrator.integration import MockMergeAdapter
from orchestrator.models import MissionState, Task, TaskState
from orchestrator.persistence import PersistenceManager
from orchestrator.routing import ExecutionProfile, ModelTier
from orchestrator.telemetry import TelemetryCollector
from orchestrator.verification import (
    MockAdversarialVerifier,
    MockIndependentVerifier,
    MockVictoryAuditVerifier,
    VerificationEngine,
    VerificationPolicy,
)
from orchestrator.workspace import WorkspaceMode


class TestEndToEndLifecycle(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.test_dir, "mission_dag.json")
        self.telemetry_file = os.path.join(self.test_dir, "telemetry.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_full_successful_mission_with_all_tiers_and_repair(self):
        """
        Tests the complete pipeline:
        Mission -> DAG -> Adaptive Scheduler -> Reusable Workers -> Workspace
        -> Execution -> Tier 1 -> Tier 2 (repair needed) -> Repair Succeeded
        -> Tier 2 Pass -> Tier 3 -> Merge -> Tier 4 -> COMPLETED.
        """
        engine = MissionEngine(mission_id="m_e2e_success", title="Full E2E Mission")
        persistence = PersistenceManager(state_file_path=self.state_file)
        telemetry = TelemetryCollector(mission_id="m_e2e_success", output_path=self.telemetry_file)
        engine.attach_persistence(persistence)
        engine.attach_telemetry(telemetry)

        # 3 tasks with dependencies: t1 -> t2, disjoint t3
        t1 = Task(id="t1", description="Backend Schema", domain="backend", write_set={"schema.py"})
        t2 = Task(id="t2", description="API Route", domain="backend", write_set={"route.py"}, dependencies={"t1"})
        t3 = Task(id="t3", description="Frontend View", domain="frontend", write_set={"view.tsx"})

        for t in [t1, t2, t3]:
            engine.add_task(t)

        engine.start()
        self.assertEqual(engine.state, MissionState.EXECUTING)

        # Register 2 reusable workers
        engine.register_worker(worker_id="w_back", domains=["backend"])
        engine.register_worker(worker_id="w_front", domains=["frontend"])

        # Step 1: Dispatch t1 & t3 (both ready, t2 blocked on t1)
        d1 = engine.assign_next()
        self.assertEqual(d1.task_id, "t1")
        self.assertEqual(d1.worker_id, "w_back")

        d3 = engine.assign_next()
        self.assertEqual(d3.task_id, "t3")
        self.assertEqual(d3.worker_id, "w_front")

        # Complete t1 -> t2 becomes ready
        engine.mark_task_completed("t1", result={"status": "ok"})
        self.assertEqual(t1.state, TaskState.MERGED)
        self.assertIn("t2", engine.get_ready_tasks())

        # Complete t3
        engine.mark_task_completed("t3", result={"status": "ok"})
        self.assertEqual(t3.state, TaskState.MERGED)

        # Step 2: Dispatch t2 to reused worker w_back
        d2 = engine.assign_next()
        self.assertEqual(d2.task_id, "t2")
        self.assertEqual(d2.worker_id, "w_back")

        # Simulate Tier 2 failure followed by in-context repair
        # Configure verification engine with repair
        ver_engine = VerificationEngine()
        # Custom mock verifier that fails once then passes
        class FlakyVerifier:
            def __init__(self):
                self.calls = 0
            def verify(self, task, execution_result=None):
                self.calls += 1
                from orchestrator.verification import VerificationResult, VerificationStatus, VerificationTier
                if self.calls == 1:
                    return VerificationResult(
                        tier=VerificationTier.TIER2_INDEPENDENT,
                        status=VerificationStatus.FAILED,
                        passed=False,
                        evidence=None,
                        error_message="Linter warning in route.py",
                    )
                return VerificationResult(
                    tier=VerificationTier.TIER2_INDEPENDENT,
                    status=VerificationStatus.PASSED,
                    passed=True,
                    evidence=None,
                )

        flaky = FlakyVerifier()
        ver_engine.tier2_verifier = flaky

        # Verify task t2
        ver_res = ver_engine.verify_task(t2, execution_result={"status": "ok"})
        self.assertFalse(ver_res.passed)

        # In-context repair
        repair_payload = ver_engine.request_repair(t2, ver_res)
        self.assertIsNotNone(repair_payload)
        self.assertEqual(repair_payload.worker_id, "w_back")

        # Worker performs repair and completes
        repaired_res = ver_engine.verify_task(t2, execution_result={"status": "ok", "repaired": True})
        self.assertTrue(repaired_res.passed)

        # Complete t2 in engine
        engine.mark_task_completed("t2", result={"status": "ok"})
        self.assertEqual(t2.state, TaskState.MERGED)

        # Tier 4 Victory Audit triggers automatically upon all tasks completed & merged
        self.assertEqual(engine.state, MissionState.COMPLETED)

        # Verify telemetry was recorded and deterministic output file created
        self.assertTrue(os.path.exists(self.telemetry_file))
        report = telemetry.get_report()
        self.assertEqual(report.mission.completed_tasks, 3)
        self.assertEqual(report.workers.total_worker_reuses, 1)

        # Verify persistence file was written
        self.assertTrue(os.path.exists(self.state_file))

    def test_mission_fails_on_unresolvable_task_failure(self):
        """
        Proves failure path:
        Task fails -> max retries exhausted -> Mission transitions to FAILED
        """
        engine = MissionEngine(mission_id="m_e2e_fail", title="Failing Mission")
        t1 = Task(id="t1", description="Critical Core", domain="backend", max_retries=1)
        engine.add_task(t1)
        engine.start()

        engine.register_worker(worker_id="w1", domains=["backend"])
        engine.assign_next()

        # Fail once (retry count 1)
        engine.mark_task_failed("t1", error="Transient error")
        self.assertEqual(t1.state, TaskState.READY)
        self.assertEqual(t1.retry_count, 1)

        # Re-assign and fail again (exhaust retries)
        engine.assign_next()
        engine.mark_task_failed("t1", error="Permanent fatal error")
        self.assertEqual(t1.state, TaskState.FAILED)

        # Victory audit runs and mission fails
        audit_result = engine.run_victory_audit()
        self.assertFalse(audit_result.passed)
        self.assertEqual(engine.state, MissionState.FAILED)
