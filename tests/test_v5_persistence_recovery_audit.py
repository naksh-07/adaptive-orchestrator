"""
Tests for Adaptive Orchestrator v5 - Persistence & Crash Recovery Audit.
Exhaustively validates scenarios A through G as specified in the v5 Architecture:
  A. Crash during RUNNING -> reverts to READY (retry_count incremented, stale references cleared)
     or FAILED if retries exhausted.
  B. Crash during VERIFYING -> reverts to READY with retry accounting (never blindly marked PASSED).
  C. Crash after PASSED before MERGED -> preserves PASSED state without duplicating merge state.
  D. Crash after MERGED -> recovery remains idempotent.
  E. Stale workspace locks -> active workspace ownership neutralized.
  F. Corrupt checkpoint -> fails explicitly with ValueError.
  G. Repeated recovery -> recovery from same checkpoint does not endlessly mutate or increment state.
"""

import json
import os
import shutil
import tempfile
import unittest

from orchestrator.engine import MissionEngine
from orchestrator.models import EventType, MissionState, Task, TaskState
from orchestrator.persistence.checkpoint import CheckpointPolicy, CheckpointTrigger
from orchestrator.persistence.manager import PersistenceManager, atomic_write_json
from orchestrator.workspace.models import WorkspaceMode
from orchestrator.workspace.registry import WorkspaceRegistry


class TestPersistenceRecoveryAudit(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.test_dir, "mission_dag.json")
        self.manager = PersistenceManager(state_file_path=self.state_file)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_scenario_a_crash_during_running(self):
        """Scenario A: Crash during RUNNING -> READY, retry_count += 1, stale links cleared."""
        engine = MissionEngine(mission_id="m_crash_running", title="Crash Running Test")
        t1 = Task(id="t1", description="Backend worker task", domain="backend", max_retries=2)
        engine.add_task(t1)
        engine.start()

        # Simulate task in RUNNING state with worker and workspace
        t1.transition_to(TaskState.RUNNING)
        t1.assigned_worker_id = "worker-01"
        t1.workspace_path = "/tmp/stale/workspace/path"
        t1.retry_count = 0

        # Save checkpoint while task is RUNNING
        self.manager.save_mission(engine, trigger=CheckpointTrigger.EXPLICIT)

        # Restore fresh engine
        restored_engine, report = self.manager.restore_mission_engine(self.state_file)
        restored_t1 = restored_engine.graph.get_task("t1")

        self.assertEqual(restored_t1.status, TaskState.READY)
        self.assertEqual(restored_t1.retry_count, 1)
        self.assertIsNone(restored_t1.assigned_worker_id)
        self.assertIsNone(restored_t1.workspace_path)
        self.assertTrue(restored_engine.ready_queue.contains("t1"))

        # Also test when retries are exhausted
        t1.retry_count = 2  # max_retries is 2
        self.manager.save_mission(engine, trigger=CheckpointTrigger.EXPLICIT)
        restored_engine2, report2 = self.manager.restore_mission_engine(self.state_file)
        restored_t1_exhausted = restored_engine2.graph.get_task("t1")
        self.assertEqual(restored_t1_exhausted.status, TaskState.FAILED)
        self.assertIn("exhausted", report2.interrupted_tasks[0].reason)

    def test_scenario_b_crash_during_verifying(self):
        """Scenario B: Crash during VERIFYING -> READY with retry accounting; not marked PASSED."""
        engine = MissionEngine(mission_id="m_crash_verifying", title="Crash Verifying Test")
        t1 = Task(id="t1", description="Critical calculation", domain="backend", max_retries=2)
        engine.add_task(t1)
        engine.start()

        t1.transition_to(TaskState.RUNNING)
        t1.transition_to(TaskState.VERIFYING)
        t1.assigned_worker_id = "worker-verif"
        t1.retry_count = 0

        self.manager.save_mission(engine, trigger=CheckpointTrigger.VERIFICATION_RESULT)

        # Recover
        restored_engine, report = self.manager.restore_mission_engine(self.state_file)
        recovered_task = restored_engine.graph.get_task("t1")

        # Crucial invariant: never blindly marked PASSED merely because checkpoint exists
        self.assertNotEqual(recovered_task.status, TaskState.PASSED)
        self.assertEqual(recovered_task.status, TaskState.READY)
        self.assertEqual(recovered_task.retry_count, 1)
        self.assertIsNone(recovered_task.assigned_worker_id)
        self.assertTrue(restored_engine.ready_queue.contains("t1"))

    def test_scenario_c_crash_after_passed_before_merged(self):
        """Scenario C: Crash after PASSED but before MERGED -> preserves PASSED state."""
        engine = MissionEngine(mission_id="m_passed_unmerged", title="Passed Unmerged Test")
        t1 = Task(id="t1", description="Feature branch task", domain="backend", workspace_mode="branch", write_set={"file.py"})
        engine.add_task(t1)
        engine.start()

        t1.transition_to(TaskState.RUNNING)
        t1.transition_to(TaskState.VERIFYING)
        t1.transition_to(TaskState.PASSED)
        t1.result = {"output": "ok"}

        self.manager.save_mission(engine, trigger=CheckpointTrigger.TASK_COMPLETED)

        # Recover
        restored_engine, report = self.manager.restore_mission_engine(self.state_file)
        recovered_task = restored_engine.graph.get_task("t1")

        self.assertEqual(recovered_task.status, TaskState.PASSED)
        self.assertEqual(recovered_task.result, {"output": "ok"})
        # Should not be in ready queue because it's PASSED
        self.assertFalse(restored_engine.ready_queue.contains("t1"))

    def test_scenario_d_crash_after_merged(self):
        """Scenario D: Crash after MERGED -> recovery remains idempotent and stable."""
        engine = MissionEngine(mission_id="m_merged", title="Merged Task Test")
        t1 = Task(id="t1", description="Merged task", domain="backend")
        engine.add_task(t1)
        engine.start()

        t1.transition_to(TaskState.RUNNING)
        t1.transition_to(TaskState.PASSED)
        t1.transition_to(TaskState.MERGED)

        self.manager.save_mission(engine, trigger=CheckpointTrigger.MERGE_RESULT)

        restored_engine, report = self.manager.restore_mission_engine(self.state_file)
        recovered_task = restored_engine.graph.get_task("t1")
        self.assertEqual(recovered_task.status, TaskState.MERGED)
        self.assertFalse(restored_engine.ready_queue.contains("t1"))

    def test_scenario_e_stale_workspace_lock_neutralization(self):
        """Scenario E: Stale active workspace locks are neutralized on recovery."""
        ws_reg = WorkspaceRegistry()
        engine = MissionEngine(mission_id="m_lock", title="Stale Lock Test", workspace_registry=ws_reg)
        t1 = Task(id="t1", description="Locked task", domain="backend", write_set={"shared.py"})
        engine.add_task(t1)
        engine.start()

        t1.transition_to(TaskState.RUNNING)
        t1.assigned_worker_id = "worker-lock"
        ws_reg.acquire("t1", "worker-lock", WorkspaceMode.IN_PLACE, set(), {"shared.py"})
        self.assertTrue(ws_reg.is_locked("t1"))

        self.manager.save_mission(engine)

        # Restore into clean fresh registry
        restored_engine, report = self.manager.restore_mission_engine(self.state_file)
        self.assertFalse(restored_engine.workspace_registry.is_locked("t1"))
        self.assertEqual(len(restored_engine.workspace_registry.get_active_records()), 0)

    def test_scenario_f_corrupt_checkpoint_rejection(self):
        """Scenario F: Corrupt or truncated checkpoint files must fail explicitly."""
        with open(self.state_file, "w", encoding="utf-8") as f:
            f.write("{corrupt_json: [invalid")

        with self.assertRaises(ValueError):
            self.manager.restore_mission_engine(self.state_file)

        # Missing required structure
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump({"unrelated": 123}, f)

        with self.assertRaises(ValueError):
            self.manager.restore_mission_engine(self.state_file)

    def test_scenario_g_repeated_recovery_idempotency(self):
        """Scenario G: Running recovery twice against same checkpoint does not endlessly mutate."""
        engine = MissionEngine(mission_id="m_repeat", title="Repeat Recovery Test")
        t1 = Task(id="t1", description="Interrupted task", domain="backend", max_retries=3)
        engine.add_task(t1)
        engine.start()
        t1.transition_to(TaskState.RUNNING)
        t1.retry_count = 0

        self.manager.save_mission(engine)

        # First recovery
        engine_rec1, report1 = self.manager.restore_mission_engine(self.state_file)
        task_rec1 = engine_rec1.graph.get_task("t1")
        self.assertEqual(task_rec1.status, TaskState.READY)
        self.assertEqual(task_rec1.retry_count, 1)

        # Second recovery against same checkpoint file
        engine_rec2, report2 = self.manager.restore_mission_engine(self.state_file)
        task_rec2 = engine_rec2.graph.get_task("t1")
        self.assertEqual(task_rec2.status, TaskState.READY)
        self.assertEqual(task_rec2.retry_count, 1)  # Stays at 1, does NOT increment endlessly!


if __name__ == "__main__":
    unittest.main()
