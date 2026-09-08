"""
Tests for Adaptive Orchestrator v5 - Idempotency Test Matrix.
Validates repeated calls across 10 critical lifecycle operations:
  1. Task completion (mark_task_completed / scheduler.complete_task)
  2. Task verification (verifier.verify)
  3. Repair request (repair_coordinator.build_repair_payload)
  4. Worker release (worker_registry.release / release_worker)
  5. Workspace release (workspace_registry.release)
  6. Merge request (integration_manager.submit_for_merge / enqueue)
  7. Checkpoint save (persistence_manager.save_mission)
  8. Checkpoint restore (persistence_manager.restore_mission_engine)
  9. Tier 4 Victory Audit (tier4_verifier.audit_mission)
  10. Mission finalization (engine.finalize_mission / complete_mission)
"""

import os
import shutil
import tempfile
import unittest

from orchestrator.engine import MissionEngine
from orchestrator.integration.manager import IntegrationManager
from orchestrator.models import MissionState, Task, TaskState
from orchestrator.persistence.checkpoint import CheckpointTrigger
from orchestrator.persistence.manager import PersistenceManager
from orchestrator.verification import (
    RepairCoordinator,
    Tier1SelfTestVerifier,
    Tier4VictoryAuditVerifier,
    VerificationPolicy,
)
from orchestrator.workers.models import Worker, WorkerState
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workspace.models import WorkspaceMode, WorkspaceReleaseState
from orchestrator.workspace.registry import WorkspaceRegistry


class TestIdempotencyMatrix(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.test_dir, "mission_state.json")
        self.persistence = PersistenceManager(state_file_path=self.state_file)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_1_task_completion_idempotency(self):
        """Repeated task completion returns existing outcome without crashing or corrupting state."""
        engine = MissionEngine(mission_id="m_idem_comp", title="Completion Idempotency")
        t1 = Task(id="t1", domain="backend")
        engine.add_task(t1)
        engine.start()

        # First completion
        task_res1, newly1 = engine.mark_task_completed("t1", result={"val": 42})
        self.assertEqual(task_res1.status, TaskState.PASSED)
        self.assertEqual(task_res1.result, {"val": 42})

        # Repeated completion
        task_res2, newly2 = engine.mark_task_completed("t1", result={"val": 99})
        self.assertEqual(task_res2.status, TaskState.PASSED)
        self.assertEqual(task_res2.result, {"val": 42})  # Retains original verified result
        self.assertEqual(newly2, [])

    def test_2_task_verification_idempotency(self):
        """Repeated verification calls on the same task produce identical deterministic results."""
        t1 = Task(id="t1", domain="backend", write_set={"foo.py"})
        verifier = Tier1SelfTestVerifier()
        policy = VerificationPolicy(tier1_enabled=True)

        res1 = verifier.verify(t1, policy=policy)
        res2 = verifier.verify(t1, policy=policy)

        self.assertEqual(res1.passed, res2.passed)
        self.assertEqual(res1.tier, res2.tier)
        self.assertEqual(len(res1.evidences), len(res2.evidences))

    def test_3_repair_request_idempotency(self):
        """Repeated repair payload building for the same defect is deterministic."""
        t1 = Task(id="t1", domain="backend")
        coordinator = RepairCoordinator()
        verifier = Tier1SelfTestVerifier(default_success=False)
        verif_res = verifier.verify(t1)

        payload1 = coordinator.build_repair_payload(t1, verif_res)
        payload2 = coordinator.build_repair_payload(t1, verif_res)

        self.assertEqual(payload1.task_id, payload2.task_id)
        self.assertEqual(payload1.failure_classification, payload2.failure_classification)
        self.assertEqual(payload1.repair_attempt, payload2.repair_attempt)

    def test_4_worker_release_idempotency(self):
        """Releasing an already idle/released worker is safely idempotent."""
        registry = WorkerRegistry()
        w1 = Worker(worker_id="w1", domain="backend", max_concurrency=1)
        registry.register_worker(w1)

        w1.assign_task("t1")
        self.assertEqual(w1.state, WorkerState.BUSY)

        # First release
        registry.release_worker("w1", success=True)
        self.assertEqual(w1.state, WorkerState.IDLE)

        # Repeated release
        registry.release_worker("w1", success=True)
        self.assertEqual(w1.state, WorkerState.IDLE)

    def test_5_workspace_release_idempotency(self):
        """Releasing a workspace lock multiple times returns safely without raising."""
        ws_reg = WorkspaceRegistry()
        ws_reg.acquire("t1", "w1", WorkspaceMode.IN_PLACE, set(), {"file.py"})
        self.assertTrue(ws_reg.is_locked("t1"))

        # First release
        res1 = ws_reg.release("t1", state=WorkspaceReleaseState.RELEASED)
        self.assertTrue(res1)
        self.assertFalse(ws_reg.is_locked("t1"))

        # Repeated release on already released task
        res2 = ws_reg.release("t1", state=WorkspaceReleaseState.RELEASED)
        self.assertFalse(res2)
        self.assertFalse(ws_reg.is_locked("t1"))

    def test_6_merge_request_idempotency(self):
        """Submitting or processing a task that is already merged returns safely."""
        int_mgr = IntegrationManager()
        t1 = Task(id="t1", domain="backend", write_set={"a.py"}, branch_name="b-t1")

        req1 = int_mgr.submit_for_merge(t1, worker_id="w1")
        self.assertIsNotNone(req1)
        results = int_mgr.process_pending_merges()
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

        # Empty queue processing
        results_empty = int_mgr.process_pending_merges()
        self.assertEqual(len(results_empty), 0)

    def test_7_checkpoint_save_idempotency(self):
        """Repeated atomic checkpoint saves overwrite safely and preserve valid state."""
        engine = MissionEngine(mission_id="m_idem_save", title="Save Idempotency")
        engine.add_task(Task(id="t1"))
        engine.start()

        state1 = self.persistence.save_mission(engine, trigger=CheckpointTrigger.EXPLICIT)
        state2 = self.persistence.save_mission(engine, trigger=CheckpointTrigger.EXPLICIT)

        self.assertTrue(os.path.exists(self.state_file))
        loaded = self.persistence.load_mission(self.state_file)
        self.assertEqual(loaded.mission_id, "m_idem_save")
        self.assertIn("t1", loaded.tasks)

    def test_8_checkpoint_restore_idempotency(self):
        """Restoring multiple times from the same snapshot produces equivalent engine states."""
        engine = MissionEngine(mission_id="m_idem_restore", title="Restore Idempotency")
        engine.add_task(Task(id="t1", domain="backend"))
        engine.start()
        engine.mark_task_completed("t1")
        self.persistence.save_mission(engine)

        eng_a, rep_a = self.persistence.restore_mission_engine(self.state_file)
        eng_b, rep_b = self.persistence.restore_mission_engine(self.state_file)

        self.assertEqual(eng_a.mission.mission_id, eng_b.mission.mission_id)
        self.assertEqual(eng_a.graph.get_task("t1").status, eng_b.graph.get_task("t1").status)
        self.assertEqual(rep_a.recovered_tasks_count, rep_b.recovered_tasks_count)

    def test_9_tier4_audit_idempotency(self):
        """Repeated Tier 4 audits yield the exact same verdict and failure count."""
        engine = MissionEngine(mission_id="m_idem_audit", title="Audit Idempotency")
        engine.add_task(Task(id="t1", domain="backend"))
        engine.start()
        engine.mark_task_completed("t1")

        auditor = Tier4VictoryAuditVerifier()
        audit1 = auditor.audit_mission(engine)
        audit2 = auditor.audit_mission(engine)

        self.assertEqual(audit1.passed, audit2.passed)
        self.assertEqual(audit1.summary, audit2.summary)
        self.assertEqual(audit1.unresolved_failures, audit2.unresolved_failures)

    def test_10_mission_finalization_idempotency(self):
        """Completing a mission multiple times maintains COMPLETED state safely."""
        engine = MissionEngine(mission_id="m_idem_fin", title="Finalization Idempotency")
        engine.add_task(Task(id="t1"))
        engine.start()
        engine.mark_task_completed("t1")

        # Engine transitions to COMPLETED
        self.assertEqual(engine.mission.state, MissionState.COMPLETED)

        # Repeated complete / completion check calls remain safely in COMPLETED
        engine._check_mission_completion()
        self.assertEqual(engine.mission.state, MissionState.COMPLETED)

        engine.mission.transition_to(MissionState.COMPLETED)
        self.assertEqual(engine.mission.state, MissionState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
