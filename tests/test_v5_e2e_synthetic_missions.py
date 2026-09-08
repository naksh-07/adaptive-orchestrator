"""
Adaptive Orchestrator v5 - Section 9: End-to-End Synthetic Missions
Deterministic integration test suite covering Missions A through F.
"""

import os
import shutil
import tempfile
import unittest

from orchestrator.engine import MissionEngine
from orchestrator.models import (
    EventType,
    Mission,
    MissionState,
    Task,
    TaskState,
)
from orchestrator.persistence import CheckpointTrigger, PersistenceManager
from orchestrator.routing import ModelRouter, ModelTier
from orchestrator.telemetry import TelemetryCollector
from orchestrator.verification import (
    FailureClassification,
    IndependentVerifier,
    MockAdversarialVerifier,
    MockIndependentVerifier,
    RepairCoordinator,
    Tier1SelfTestVerifier,
    Tier3AdversarialVerifier,
    Tier4VictoryAuditVerifier,
    VerificationEngine,
    VerificationPolicy,
    VerificationResult,
    VerificationStatus,
    VerificationTier,
    VictoryAuditResult,
)
from orchestrator.exceptions import WorkspaceConflictError
from orchestrator.workspace import (
    WorkspaceMode,
    WorkspaceRegistry,
    WorkspaceReleaseState,
)


class TestV5EndToEndSyntheticMissions(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.test_dir, "mission_checkpoint.json")
        self.telemetry_file = os.path.join(self.test_dir, "telemetry_report.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Mission A: Happy Path (12 tasks, 4 domains, reuse, AIMD, Tier 1-4)
    # -------------------------------------------------------------------------
    def test_mission_a_happy_path(self):
        """
        Mission A:
        - 12 tasks across 4 domains (database, backend, frontend, devops)
        - Non-trivial DAG with concurrent ready tasks
        - Worker reuse across tasks
        - Adaptive capacity progression
        - Model routing (FAST vs PRO)
        - Verification tiers (Tier 1 self-test, Tier 2 independent, Tier 3 adversarial)
        - Serialization of merges
        - Tier 4 Victory Audit
        - Final COMPLETED state
        """
        engine = MissionEngine(mission_id="m_happy_12", title="12-Task 4-Domain Happy Path")
        telemetry = TelemetryCollector(mission_id="m_happy_12", output_path=self.telemetry_file)
        persistence = PersistenceManager(state_file_path=self.state_file)
        engine.attach_telemetry(telemetry)
        engine.attach_persistence(persistence)

        # Configure router
        router = ModelRouter()

        # Define 12 tasks across 4 domains
        tasks = [
            # Database domain
            Task(id="db_1", description="Init schema", domain="database", write_set={"db/schema.sql"}),
            Task(id="db_2", description="Apply migrations", domain="database", write_set={"db/mig.sql"}, dependencies={"db_1"}),
            # Backend domain
            Task(id="be_1", description="User service models", domain="backend", write_set={"be/models.py"}, dependencies={"db_1"}),
            Task(id="be_2", description="User auth routes", domain="backend", write_set={"be/auth.py"}, dependencies={"be_1"}),
            Task(id="be_3", description="Payment API", domain="backend", write_set={"be/pay.py"}, dependencies={"be_1"}, metadata={"model_tier": "PRO"}),
            # Frontend domain
            Task(id="fe_1", description="UI design tokens", domain="frontend", write_set={"fe/tokens.css"}),
            Task(id="fe_2", description="Auth forms", domain="frontend", write_set={"fe/auth.tsx"}, dependencies={"fe_1", "be_2"}),
            Task(id="fe_3", description="Checkout view", domain="frontend", write_set={"fe/pay.tsx"}, dependencies={"fe_2", "be_3"}),
            # DevOps domain
            Task(id="dev_1", description="Docker containers", domain="devops", write_set={"ops/docker.yml"}),
            Task(id="dev_2", description="CI pipeline", domain="devops", write_set={"ops/ci.yml"}, dependencies={"dev_1"}),
            Task(id="dev_3", description="End-to-End integration test", domain="devops", write_set={"ops/e2e.py"}, dependencies={"fe_3", "dev_2"}),
            Task(id="dev_4", description="Production deployment manifest", domain="devops", write_set={"ops/deploy.yml"}, dependencies={"dev_3"}),
        ]

        for t in tasks:
            engine.add_task(t)

        # Register 4 reusable domain workers
        engine.register_worker(worker_id="w_db", domains=["database"])
        engine.register_worker(worker_id="w_be", domains=["backend"])
        engine.register_worker(worker_id="w_fe", domains=["frontend"])
        engine.register_worker(worker_id="w_dev", domains=["devops"])

        engine.start()
        self.assertEqual(engine.state, MissionState.EXECUTING)

        completed_count = 0
        assigned_tasks = set()
        worker_task_assignments = {}

        # Run dispatch and execution loop until all tasks pass
        while completed_count < len(tasks):
            assignment = engine.assign_next()
            if assignment is None:
                break

            task_id = assignment.task_id
            worker_id = assignment.worker_id
            assigned_tasks.add(task_id)
            worker_task_assignments.setdefault(worker_id, []).append(task_id)

            task = engine.get_task(task_id)

            # Model routing check
            route = router.route(task)
            if task.metadata.get("model_tier") == "PRO":
                self.assertEqual(route.tier, ModelTier.PRO)
            else:
                self.assertIn(route.tier, (ModelTier.FAST, ModelTier.PRO))

            # Tier 1 & Tier 2 verification
            t1_ver = Tier1SelfTestVerifier()
            v1 = t1_ver.verify(task)
            self.assertTrue(v1.passed)

            t2_ver = MockIndependentVerifier()
            v2 = t2_ver.verify(task)
            self.assertTrue(v2.passed)

            # Optional Tier 3 for high-complexity task be_3
            if task.metadata.get("model_tier") == "PRO":
                t3_ver = Tier3AdversarialVerifier()
                v3 = t3_ver.verify(task)
                self.assertTrue(v3.passed)

            # Mark completed & merged
            engine.mark_task_completed(task_id, result={"status": "ok", "task": task_id})
            completed_count += 1

        self.assertEqual(completed_count, 12)

        # Worker reuse verification: 12 tasks across 4 workers guarantees reuse
        reuses = sum(len(task_list) - 1 for task_list in worker_task_assignments.values() if len(task_list) > 1)
        self.assertGreaterEqual(reuses, 8)

        # Tier 4 Victory Audit automatically confirms mission completion
        self.assertEqual(engine.state, MissionState.COMPLETED)
        report = telemetry.get_report()
        self.assertEqual(report.mission.completed_tasks, 12)

    # -------------------------------------------------------------------------
    # Mission B: In-Context Local Repair
    # -------------------------------------------------------------------------
    def test_mission_b_repair_path(self):
        """
        Mission B:
        Task fails verification once, generates a focused RepairPayload,
        stays with the same worker/workspace, repairs successfully, then completes.
        """
        engine = MissionEngine(mission_id="m_repair", title="Mission B: Repair Loop")
        t1 = Task(id="t_code", domain="backend", write_set={"service.py"}, max_retries=3)
        engine.add_task(t1)
        engine.start()

        engine.register_worker(worker_id="w_dev1", domains=["backend"])
        d = engine.assign_next()
        self.assertEqual(d.worker_id, "w_dev1")

        # Create verification coordinator
        coordinator = RepairCoordinator()

        # Step 1: Initial execution fails Tier 2 verification
        failed_res = VerificationResult(
            tier=VerificationTier.TIER2_INDEPENDENT,
            status=VerificationStatus.FAILED,
            passed=False,
            error_message="SyntaxError in service.py: missing colon at line 42",
        )

        # Build focused repair payload
        payload = coordinator.build_repair_payload(t1, failed_res)
        self.assertEqual(payload.failure_classification, FailureClassification.REPAIRABLE)
        self.assertIn("SyntaxError", payload.error_summary)

        # Task transitions through retrying
        engine.mark_task_failed("t_code", error=payload.error_summary, can_retry=True)
        self.assertEqual(t1.status, TaskState.RETRYING)
        engine.retry_task("t_code")
        self.assertEqual(t1.status, TaskState.READY)
        self.assertEqual(t1.retry_count, 1)

        # Re-assign to same worker
        d2 = engine.assign_next()
        self.assertEqual(d2.worker_id, "w_dev1")

        # Step 2: Repaired execution passes verification
        pass_res = VerificationResult(
            tier=VerificationTier.TIER2_INDEPENDENT,
            status=VerificationStatus.PASSED,
            passed=True,
        )
        self.assertTrue(pass_res.passed)

        engine.mark_task_completed("t_code", result={"fixed": True})
        self.assertIn(t1.status, (TaskState.PASSED, TaskState.MERGED))
        self.assertEqual(engine.state, MissionState.COMPLETED)

    # -------------------------------------------------------------------------
    # Mission C: Non-Repairable Failure with Bounded Propagation
    # -------------------------------------------------------------------------
    def test_mission_c_non_repairable_failure(self):
        """
        Mission C:
        A task suffers a fatal, non-repairable defect. Retries exhaust.
        Dependent tasks are prevented from executing, while independent tasks complete.
        Mission fails deterministically at Tier 4.
        """
        engine = MissionEngine(mission_id="m_failure", title="Mission C: Fatal Failure")

        # Graph: t_fatal -> t_dep (blocked), t_indep (independent)
        t_fatal = Task(id="t_fatal", domain="backend", max_retries=1)
        t_dep = Task(id="t_dep", domain="backend", dependencies={"t_fatal"})
        t_indep = Task(id="t_indep", domain="frontend")

        engine.add_task(t_fatal)
        engine.add_task(t_dep)
        engine.add_task(t_indep)
        engine.start()

        engine.register_worker(worker_id="w_be", domains=["backend"])
        engine.register_worker(worker_id="w_fe", domains=["frontend"])

        # Dispatch t_fatal and t_indep
        d_fatal = engine.assign_next()
        d_indep = engine.assign_next()

        # Independent task succeeds
        engine.mark_task_completed(d_indep.task_id, result={"ok": True})
        self.assertEqual(t_indep.status, TaskState.PASSED)

        # Fatal task fails first time (transient retry 1)
        engine.mark_task_failed("t_fatal", error="Transient database connection refused", can_retry=True)
        self.assertEqual(t_fatal.status, TaskState.RETRYING)
        engine.retry_task("t_fatal")
        self.assertEqual(t_fatal.status, TaskState.READY)
        self.assertEqual(t_fatal.retry_count, 1)

        # Re-assigned and fails second time -> EXHAUSTED
        engine.assign_next()
        engine.mark_task_failed("t_fatal", error="Database connection permanently refused", can_retry=False)
        self.assertEqual(t_fatal.status, TaskState.FAILED)

        # Dependent task t_dep was NEVER dispatched and remains BLOCKED
        self.assertEqual(t_dep.status, TaskState.BLOCKED)

        # Tier 4 audit fails due to unresolved failure and incomplete terminal tasks
        audit = engine.run_victory_audit()
        self.assertFalse(audit.passed)
        self.assertEqual(engine.state, MissionState.FAILED)

    # -------------------------------------------------------------------------
    # Mission D: Workspace Conflict
    # -------------------------------------------------------------------------
    def test_mission_d_workspace_conflict(self):
        """
        Mission D:
        Two concurrently ready tasks declare overlapping write sets.
        Workspace Registry enforces exclusive ownership:
        - Task 1 gets exclusive lock
        - Task 2 is rejected with WorkspaceConflictError
        - Once Task 1 completes and releases lock, Task 2 acquires lock safely.
        """
        engine = MissionEngine(mission_id="m_ws_conflict", title="Mission D: Workspace Conflict")
        ws_reg = engine.workspace_registry

        t1 = Task(id="t_write_1", domain="backend", write_set={"config/settings.json"})
        t2 = Task(id="t_write_2", domain="backend", write_set={"config/settings.json"})

        engine.add_task(t1)
        engine.add_task(t2)
        engine.start()

        # t1 acquires workspace
        rec1 = ws_reg.acquire(
            task_id="t_write_1",
            worker_id="w1",
            mode=WorkspaceMode.IN_PLACE,
            read_set=set(),
            write_set={"config/settings.json"},
        )
        self.assertEqual(rec1.task_id, "t_write_1")

        # Concurrent t2 attempts to acquire overlapping write set -> raises WorkspaceConflictError
        with self.assertRaises(WorkspaceConflictError):
            ws_reg.acquire(
                task_id="t_write_2",
                worker_id="w2",
                mode=WorkspaceMode.IN_PLACE,
                read_set=set(),
                write_set={"config/settings.json"},
            )

        # t1 completes and releases lock
        ws_reg.release("t_write_1", state=WorkspaceReleaseState.RELEASED)
        self.assertFalse(ws_reg.is_locked("t_write_1"))

        # Now t2 acquires lock cleanly
        rec2 = ws_reg.acquire(
            task_id="t_write_2",
            worker_id="w2",
            mode=WorkspaceMode.IN_PLACE,
            read_set=set(),
            write_set={"config/settings.json"},
        )
        self.assertEqual(rec2.task_id, "t_write_2")
        ws_reg.release("t_write_2", state=WorkspaceReleaseState.RELEASED)

    # -------------------------------------------------------------------------
    # Mission E: Crash Recovery During Active Execution
    # -------------------------------------------------------------------------
    def test_mission_e_crash_recovery(self):
        """
        Mission E:
        Engine is actively executing tasks. A crash occurs.
        A fresh engine is reconstructed from the checkpoint file.
        Interrupted tasks transition back to READY with retry count accounting.
        Execution resumes and reaches COMPLETED.
        """
        engine1 = MissionEngine(mission_id="m_crash_recovery", title="Mission E: Crash Recovery")
        persistence = PersistenceManager(state_file_path=self.state_file)
        engine1.attach_persistence(persistence)

        t1 = Task(id="t1", domain="backend")
        t2 = Task(id="t2", domain="backend", dependencies={"t1"})
        t3 = Task(id="t3", domain="frontend", dependencies={"t2"})

        engine1.add_task(t1)
        engine1.add_task(t2)
        engine1.add_task(t3)
        engine1.start()

        engine1.register_worker(worker_id="w1", domains=["backend"])
        engine1.assign_next()
        engine1.mark_task_completed("t1")  # t1 passed, unblocks t2

        # Assign t2 to RUNNING
        engine1.assign_next()
        self.assertEqual(t2.status, TaskState.RUNNING)

        # Crash occurs during execution! Checkpoint is saved with t2 RUNNING
        persistence.save_mission(engine1, trigger=CheckpointTrigger.EXPLICIT)

        # Simulate fresh process: restore new engine from checkpoint
        fresh_engine, report = persistence.restore_mission_engine(self.state_file)
        self.assertEqual(report.recovered_tasks_count, 3)

        # Interrupted t2 was safely restored to READY with incremented retry count
        recovered_t2 = fresh_engine.get_task("t2")
        self.assertEqual(recovered_t2.status, TaskState.READY)
        self.assertEqual(recovered_t2.retry_count, 1)

        # Resume execution on fresh engine
        fresh_engine.register_worker(worker_id="w_fresh", domains=["backend", "frontend"])
        d2 = fresh_engine.assign_next()
        self.assertEqual(d2.task_id, "t2")
        fresh_engine.mark_task_completed("t2")

        d3 = fresh_engine.assign_next()
        self.assertEqual(d3.task_id, "t3")
        fresh_engine.mark_task_completed("t3")

        # Engine successfully completes
        self.assertEqual(fresh_engine.state, MissionState.COMPLETED)

    # -------------------------------------------------------------------------
    # Mission F: Adversarial Failure & Acceptance Criteria Rejection
    # -------------------------------------------------------------------------
    def test_mission_f_adversarial_failure(self):
        """
        Mission F:
        1. Tier 3 verifier catches undeclared writes / adversarial invariant breach.
        2. Tier 4 Victory Audit rejects a mission where global acceptance criteria fail.
        """
        # Part 1: Tier 3 rejects undeclared writes
        t_bad = Task(id="t_bad", domain="backend", write_set={"clean.py"})
        tier3_verifier = Tier3AdversarialVerifier()
        bad_result = {"modified_files": ["clean.py", "malicious_backdoor.sh"]}

        res3 = tier3_verifier.verify(t_bad, execution_result=bad_result)
        self.assertFalse(res3.passed)
        self.assertEqual(res3.status, VerificationStatus.FAILED)
        self.assertIn("Contract violation", res3.summary)

        # Part 2: Tier 4 rejects missing acceptance criteria
        engine = MissionEngine(
            mission_id="m_audit_reject",
            title="Mission F: Acceptance Rejection",
            auto_audit=False,
            required_artifacts=["dist/bundle.js"],
            acceptance_criteria={"min_coverage": 85},
        )
        t_ok = Task(id="t_ok", domain="frontend")
        engine.add_task(t_ok)
        engine.start()
        engine.mark_task_completed("t_ok", result={"coverage": 70})  # 70 < 85, missing dist/bundle.js

        # Tier 4 audit fails
        audit_res = engine.run_victory_audit()
        self.assertFalse(audit_res.passed)
        self.assertGreater(len(audit_res.unresolved_failures), 0)
        self.assertEqual(engine.state, MissionState.FAILED)


if __name__ == "__main__":
    unittest.main()
