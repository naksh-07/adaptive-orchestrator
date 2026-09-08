"""
Tests for Adaptive Orchestrator v5 - Phase 6: Production Hardening, Error Boundaries & Idempotency.
"""

import os
import shutil
import tempfile
import unittest

from orchestrator.engine import MissionEngine
from orchestrator.exceptions import AdaptiveOrchestratorError
from orchestrator.integration import IntegrationManager, MergeRequest, MergeStatus, MockMergeAdapter
from orchestrator.models import Event, EventType, MissionState, Task, TaskState
from orchestrator.persistence import PersistenceManager, atomic_write_json
from orchestrator.workers.models import WorkerState
from orchestrator.workspace import WorkspaceMode


class TestProductionErrorBoundaries(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_worker_crash_does_not_corrupt_engine_state(self):
        engine = MissionEngine(mission_id="m_err1", title="Worker Crash Mission")
        t1 = Task(id="t1", description="Task 1", domain="backend", max_retries=2)
        engine.add_task(t1)
        engine.start()

        engine.register_worker(worker_id="w1", domains=["backend"])
        engine.assign_next()
        self.assertEqual(t1.state, TaskState.RUNNING)

        # Worker crashes
        engine.handle_worker_failure(worker_id="w1", reason="SIGKILL out of memory")

        # Engine remains valid, t1 is reset to READY with incremented retry count
        self.assertEqual(t1.state, TaskState.READY)
        self.assertEqual(t1.retry_count, 1)
        self.assertIsNone(t1.assigned_worker_id)
        # Worker marked DEGRADED or RECOVERING
        worker = engine.worker_registry.get("w1")
        self.assertIn(worker.state, [WorkerState.DEGRADED, WorkerState.OFFLINE])

    def test_persistence_failure_does_not_corrupt_target_file(self):
        target = os.path.join(self.test_dir, "safe_dag.json")
        atomic_write_json(target, {"valid": True, "version": 1})

        # Attempt to write non-serializable object to corrupt it
        with self.assertRaises(TypeError):
            atomic_write_json(target, {"unserializable": object()})

        # Original file must remain intact
        import json
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["version"], 1)

    def test_merge_failure_boundary(self):
        adapter = MockMergeAdapter(should_succeed=False, conflict_error="Merge conflict in a.py")
        mgr = IntegrationManager(adapter=adapter)

        req = MergeRequest(
            task_id="t1",
            source_branch="task/t1",
            target_branch="main",
            write_set={"a.py"},
        )
        mgr.enqueue(req)
        result = mgr.process_next()
        self.assertEqual(result.status, MergeStatus.CONFLICT)
        self.assertFalse(result.success)
        # Integration manager continues running without throwing an unhandled exception
        self.assertEqual(mgr.queue_length, 0)


class TestIdempotency(unittest.TestCase):
    def test_duplicate_task_completion_is_safe(self):
        engine = MissionEngine(mission_id="m_idem", title="Idempotent Mission")
        t1 = Task(id="t1", description="Task 1", domain="backend")
        engine.add_task(t1)
        engine.start()

        engine.mark_task_completed("t1", result={"out": 1})
        self.assertIn(t1.state, [TaskState.COMPLETED, TaskState.MERGED])

        # Second completion call should not raise or double-complete
        engine.mark_task_completed("t1", result={"out": 2})
        self.assertIn(t1.state, [TaskState.COMPLETED, TaskState.MERGED])

    def test_duplicate_worker_release_is_safe(self):
        engine = MissionEngine(mission_id="m_w_rel", title="Worker Release Mission")
        engine.register_worker(worker_id="w1", domains=["backend"])
        worker = engine.worker_registry.get("w1")

        # First release when IDLE is no-op
        engine.worker_registry.release("w1")
        self.assertEqual(worker.state, WorkerState.IDLE)

        # Release again
        engine.worker_registry.release("w1")
        self.assertEqual(worker.state, WorkerState.IDLE)

    def test_duplicate_workspace_release_is_safe(self):
        engine = MissionEngine(mission_id="m_ws_rel", title="Workspace Release")
        engine.workspace_registry.acquire(
            task_id="t1",
            worker_id="w1",
            mode=WorkspaceMode.IN_PLACE,
            read_set=set(),
            write_set={"file.py"},
        )
        self.assertTrue(engine.workspace_registry.is_locked("t1"))

        # Release once
        rec = engine.workspace_registry.release("t1")
        self.assertFalse(engine.workspace_registry.is_locked("t1"))

        # Release second time should not raise or corrupt state
        rec2 = engine.workspace_registry.release("t1")
        self.assertFalse(engine.workspace_registry.is_locked("t1"))


class TestNoResourceLeaks(unittest.TestCase):
    def test_no_logical_locks_remain_after_full_mission(self):
        engine = MissionEngine(mission_id="m_leak_free", title="Leak Free Mission")
        t1 = Task(id="t1", description="Task 1", domain="backend", write_set={"a.py"})
        t2 = Task(id="t2", description="Task 2", domain="frontend", write_set={"b.py"}, dependencies={"t1"})
        engine.add_task(t1)
        engine.add_task(t2)
        engine.start()

        engine.register_worker(worker_id="w1", domains=["backend", "frontend"])

        # Execute t1
        engine.assign_next()
        engine.workspace_registry.acquire(
            task_id="t1",
            worker_id="w1",
            mode=WorkspaceMode.IN_PLACE,
            read_set=set(),
            write_set={"a.py"},
        )
        engine.workspace_registry.release("t1")
        engine.mark_task_completed("t1")

        # Execute t2
        engine.assign_next()
        engine.workspace_registry.acquire(
            task_id="t2",
            worker_id="w1",
            mode=WorkspaceMode.IN_PLACE,
            read_set=set(),
            write_set={"b.py"},
        )
        engine.workspace_registry.release("t2")
        engine.mark_task_completed("t2")

        # At conclusion:
        # All workspace locks released
        self.assertEqual(len(engine.workspace_registry.get_active_locks()), 0)
        # Ready queue is 0
        self.assertEqual(engine.ready_queue_size, 0)
        # All workers returned to IDLE
        for w in engine.worker_registry.list_all():
            self.assertEqual(w.state, WorkerState.IDLE)
