"""
Tests for Adaptive Orchestrator v5 - Phase 6: Telemetry & Observability.
"""

import json
import os
import shutil
import tempfile
import unittest

from orchestrator.engine import MissionEngine
from orchestrator.models import Event, EventType, Task, TaskState
from orchestrator.routing import ExecutionProfile, ModelTier
from orchestrator.telemetry import TelemetryCollector, TelemetryReport


class TestTelemetryCollector(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.output_file = os.path.join(self.test_dir, "telemetry.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_telemetry_event_ingestion_and_metrics(self):
        engine = MissionEngine(mission_id="m_telemetry", title="Telemetry Test")
        collector = TelemetryCollector(mission_id="m_telemetry", output_path=self.output_file)
        engine.attach_telemetry(collector)

        t1 = Task(id="t1", description="Backend Task", domain="backend", write_set={"a.py"})
        t2 = Task(id="t2", description="Frontend Task", domain="frontend", write_set={"b.py"}, dependencies={"t1"})
        engine.add_task(t1)
        engine.add_task(t2)
        engine.start()

        engine.register_worker(worker_id="w1", domains=["backend", "frontend"])

        # Record routing profile for t1
        t1.execution_profile = ExecutionProfile(
            tier=ModelTier.FAST,
            reason="Simple task",
            fast_candidate=True,
            risk_score=0.1,
        )

        # Dispatch t1
        engine.assign_next()
        # Complete t1
        engine.mark_task_completed("t1", result={"status": "ok"})

        # Worker w1 is reused for t2
        t2.execution_profile = ExecutionProfile(
            tier=ModelTier.PRO,
            reason="Complex UI",
            fast_candidate=False,
            risk_score=0.8,
        )
        engine.assign_next()
        engine.mark_task_completed("t2", result={"status": "ok"})

        # Generate report
        report = collector.get_report()
        self.assertEqual(report.mission_id, "m_telemetry")
        self.assertEqual(report.mission.completed_tasks, 2)
        self.assertEqual(report.mission.failed_tasks, 0)

        # Workers metrics
        self.assertIn("w1", report.workers.workers)
        self.assertEqual(report.workers.workers["w1"].tasks_completed, 2)
        self.assertGreaterEqual(report.workers.total_worker_reuses, 1)

        # Routing metrics
        self.assertEqual(report.routing.fast_executions, 1)
        self.assertEqual(report.routing.pro_executions, 1)
        self.assertEqual(report.routing.routing_decisions, 2)

        # Save to disk
        saved_path = collector.save()
        self.assertTrue(os.path.exists(saved_path))
        with open(saved_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["mission_id"], "m_telemetry")
        self.assertEqual(data["mission"]["completed_tasks"], 2)
        self.assertEqual(data["routing"]["fast_executions"], 1)

    def test_telemetry_captures_verification_and_repair(self):
        collector = TelemetryCollector(mission_id="m_repairs")

        # Feed events directly
        collector.on_event(Event(event_type=EventType.VERIFICATION_PASSED, mission_id="m_repairs", task_id="t1", payload={"tier": "TIER_1"}))
        collector.on_event(Event(event_type=EventType.VERIFICATION_FAILED, mission_id="m_repairs", task_id="t1", payload={"tier": "TIER_2"}))
        collector.on_event(Event(event_type=EventType.REPAIR_REQUESTED, mission_id="m_repairs", task_id="t1"))
        collector.on_event(Event(event_type=EventType.REPAIR_COMPLETED, mission_id="m_repairs", task_id="t1"))
        collector.on_event(Event(event_type=EventType.VERIFICATION_PASSED, mission_id="m_repairs", task_id="t1", payload={"tier": "TIER_2"}))
        collector.on_event(Event(event_type=EventType.TIER3_CHALLENGE_PASSED, mission_id="m_repairs", task_id="t1"))
        collector.on_event(Event(event_type=EventType.TIER4_AUDIT_PASSED, mission_id="m_repairs"))

        report = collector.get_report()
        self.assertEqual(report.verification.tier1_passed, 1)
        self.assertEqual(report.verification.tier2_failed, 1)
        self.assertEqual(report.verification.tier2_passed, 1)
        self.assertEqual(report.verification.repair_attempts, 1)
        self.assertEqual(report.verification.repair_successes, 1)
        self.assertEqual(report.verification.tier3_passed, 1)
        self.assertEqual(report.verification.tier4_audits_passed, 1)
