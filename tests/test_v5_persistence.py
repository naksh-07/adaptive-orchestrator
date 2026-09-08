"""
Tests for Adaptive Orchestrator v5 - Phase 6: Mission Persistence & Crash Recovery.
"""

import json
import os
import shutil
import tempfile
import unittest

from orchestrator.engine import MissionEngine
from orchestrator.models import EventType, MissionState, Task, TaskState
from orchestrator.persistence import (
    CheckpointPolicy,
    CheckpointTrigger,
    InterruptedTaskRecovery,
    PersistenceManager,
    RecoveryReport,
    SerializedMissionState,
    atomic_write_json,
)
from orchestrator.workspace import WorkspaceMode


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_atomic_write_creates_file(self):
        target = os.path.join(self.test_dir, "test.json")
        data = {"hello": "world", "num": 42}
        atomic_write_json(target, data)
        self.assertTrue(os.path.exists(target))
        with open(target, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded, data)

    def test_atomic_write_replaces_existing_safely(self):
        target = os.path.join(self.test_dir, "test.json")
        atomic_write_json(target, {"version": 1})
        atomic_write_json(target, {"version": 2})
        with open(target, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["version"], 2)

    def test_atomic_write_creates_parent_dirs(self):
        target = os.path.join(self.test_dir, "subdir", "nested", "test.json")
        atomic_write_json(target, {"nested": True})
        self.assertTrue(os.path.exists(target))


class TestPersistenceManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.test_dir, "mission_dag.json")
        self.manager = PersistenceManager(state_file_path=self.state_file)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_save_and_load_mission_state(self):
        engine = MissionEngine(mission_id="mission_p1", title="Persistence Test")
        t1 = Task(id="t1", description="Task 1", domain="backend", write_set={"file1.py"})
        t2 = Task(id="t2", description="Task 2", domain="frontend", dependencies={"t1"})
        engine.add_task(t1)
        engine.add_task(t2)
        engine.start()

        # Save
        saved_state = self.manager.save_mission(engine)
        self.assertTrue(os.path.exists(self.state_file))
        self.assertEqual(saved_state.mission_id, "mission_p1")
        self.assertEqual(len(saved_state.tasks), 2)
        self.assertEqual(saved_state.mission_state, MissionState.EXECUTING.value)

        # Load
        loaded_state = self.manager.load_mission()
        self.assertEqual(loaded_state.mission_id, "mission_p1")
        self.assertEqual(loaded_state.title, "Persistence Test")
        self.assertIn("t1", loaded_state.tasks)
        self.assertIn("t2", loaded_state.tasks)
        self.assertEqual(loaded_state.tasks["t2"]["dependencies"], ["t1"])
        self.assertEqual(loaded_state.tasks["t1"]["write_set"], ["file1.py"])

    def test_corrupted_state_rejection(self):
        # Write corrupted JSON
        with open(self.state_file, "w", encoding="utf-8") as f:
            f.write("NOT_A_VALID_JSON{[[[")
        with self.assertRaises(ValueError):
            self.manager.load_mission()

        # Write missing required fields
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump({"something_else": 123}, f)
        with self.assertRaises(ValueError):
            self.manager.load_mission()

    def test_crash_recovery_interrupted_tasks(self):
        engine = MissionEngine(mission_id="mission_crash", title="Crash Mission")
        t1 = Task(id="t1", description="T1", domain="backend", max_retries=2)
        t2 = Task(id="t2", description="T2", domain="frontend", dependencies={"t1"})
        engine.add_task(t1)
        engine.add_task(t2)
        engine.start()

        # Register worker and assign t1
        engine.register_worker(worker_id="w1", domains=["backend"])
        engine.assign_next()  # t1 is now RUNNING with w1

        # Simulate lock acquisition
        engine.workspace_registry.acquire(
            task_id="t1",
            worker_id="w1",
            mode=WorkspaceMode.IN_PLACE,
            read_set=set(),
            write_set={"file1.py"},
        )
        self.assertTrue(engine.workspace_registry.is_locked("t1"))

        # Save mission state while t1 is RUNNING
        self.manager.save_mission(engine)

        # Now simulate process death and restore into a fresh engine
        new_engine, recovery_report = self.manager.restore_mission_engine()

        self.assertEqual(recovery_report.mission_id, "mission_crash")
        self.assertEqual(len(recovery_report.interrupted_tasks), 1)
        self.assertEqual(recovery_report.interrupted_tasks[0].task_id, "t1")
        self.assertEqual(recovery_report.interrupted_tasks[0].previous_state, "RUNNING")
        self.assertEqual(recovery_report.interrupted_tasks[0].recovered_to, "READY")

        # In new engine:
        # t1 was reset to READY, worker assignment cleared, retry count incremented
        restored_t1 = new_engine.get_task("t1")
        self.assertEqual(restored_t1.state, TaskState.READY)
        self.assertIsNone(restored_t1.assigned_worker_id)
        self.assertEqual(restored_t1.retry_count, 1)

        # Ready queue contains t1
        self.assertEqual(new_engine.ready_queue_size, 1)
        self.assertIn("t1", new_engine.get_ready_tasks())

        # Stale workspace lock was cleared
        self.assertFalse(new_engine.workspace_registry.is_locked("t1"))

    def test_interrupted_task_retries_exhausted(self):
        engine = MissionEngine(mission_id="mission_exhaust", title="Exhaust Mission")
        t1 = Task(id="t1", description="T1", domain="backend", max_retries=0)  # no retries allowed
        engine.add_task(t1)
        engine.start()

        engine.register_worker(worker_id="w1", domains=["backend"])
        engine.assign_next()  # t1 is RUNNING

        self.manager.save_mission(engine)

        # Restore
        new_engine, recovery_report = self.manager.restore_mission_engine()
        self.assertEqual(recovery_report.interrupted_tasks[0].recovered_to, "FAILED")
        restored_t1 = new_engine.get_task("t1")
        self.assertEqual(restored_t1.state, TaskState.FAILED)
        self.assertEqual(new_engine.ready_queue_size, 0)


class TestCheckpointPolicy(unittest.TestCase):
    def test_checkpoint_trigger_mapping(self):
        policy = CheckpointPolicy(enabled_triggers={CheckpointTrigger.TASK_COMPLETED})
        self.assertTrue(policy.should_checkpoint_event(EventType.TASK_COMPLETED.value))
        self.assertFalse(policy.should_checkpoint_event(EventType.TASK_ASSIGNED.value))

    def test_throttled_checkpoint(self):
        policy = CheckpointPolicy(
            enabled_triggers={CheckpointTrigger.TASK_ASSIGNED},
            throttle_seconds=10.0,
        )
        self.assertTrue(policy.should_checkpoint(CheckpointTrigger.TASK_ASSIGNED))
        # Second call immediately should be throttled
        self.assertFalse(policy.should_checkpoint(CheckpointTrigger.TASK_ASSIGNED))
