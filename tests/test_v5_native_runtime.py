#!/usr/bin/env python3
"""
Unit tests for Adaptive Orchestrator v5 Native Antigravity Runtime Integration.
Verifies:
  1. Worker native identity attributes and serialization (to_dict / from_dict)
  2. WorkerRegistry native conversation binding and lookup methods
  3. NativeExecutionAdapter fresh dispatch (invoke_subagent) vs warm dispatch (send_message)
  4. NativeExecutionAdapter targeted repair dispatch (send_message repair)
  5. Distinction between internal worker reuse and native conversation reuse
  6. Persistence serialization of native worker state and stale session neutralization
"""

import os
import sys
import unittest
import tempfile
import shutil

from orchestrator.engine import MissionEngine
from orchestrator.models import Task, TaskState
from orchestrator.routing.models import ModelTier, ExecutionProfile
from orchestrator.workers.models import Worker, WorkerState
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workers.adapter import (
    NativeExecutionAdapter,
    NativeDispatchInstruction,
    NativeRepairInstruction,
)
from orchestrator.persistence.manager import PersistenceManager
from orchestrator.workspace.models import WorkspaceMode


class TestV5NativeRuntimeIntegration(unittest.TestCase):

    def setUp(self):
        self.registry = WorkerRegistry()
        self.adapter = NativeExecutionAdapter(repo_dir="/tmp/fake_repo")

    def test_worker_native_identity_and_serialization(self):
        """Worker model should cleanly carry native identity and round-trip via to_dict / from_dict."""
        worker = Worker(
            worker_id="w_explorer_01",
            domain="exploration",
            native_agent_name="explorer",
            native_session_id="conv_native_12345",
            model_tier=ModelTier.FAST,
            resolved_model="flash",
            workspace_mode=WorkspaceMode.INHERIT,
            is_native=True,
            is_reusable=True,
        )

        self.assertEqual(worker.native_agent_name, "explorer")
        self.assertEqual(worker.native_session_id, "conv_native_12345")
        self.assertEqual(worker.resolved_model, "flash")
        self.assertTrue(worker.is_native)
        self.assertTrue(worker.is_reusable)

        d = worker.to_dict()
        self.assertEqual(d["native_agent_name"], "explorer")
        self.assertEqual(d["native_session_id"], "conv_native_12345")
        self.assertEqual(d["resolved_model"], "flash")
        self.assertTrue(d["is_native"])
        self.assertTrue(d["is_reusable"])

        restored = Worker.from_dict(d)
        self.assertEqual(restored.worker_id, worker.worker_id)
        self.assertEqual(restored.domain, worker.domain)
        self.assertEqual(restored.native_agent_name, "explorer")
        self.assertEqual(restored.native_session_id, "conv_native_12345")
        self.assertEqual(restored.resolved_model, "flash")
        self.assertEqual(restored.model_tier, ModelTier.FAST)
        self.assertEqual(restored.workspace_mode, WorkspaceMode.INHERIT)
        self.assertTrue(restored.is_native)
        self.assertTrue(restored.is_reusable)

    def test_worker_registry_native_binding_and_lookup(self):
        """WorkerRegistry should bind native conversation IDs and find workers by conversation ID or native agent name."""
        worker = Worker(
            worker_id="w_impl_01",
            domain="backend",
            native_agent_name="implementer",
            is_native=True,
        )
        self.registry.register_worker(worker)

        # Before binding
        self.assertIsNone(worker.native_session_id)
        self.assertIsNone(self.registry.find_by_conversation_id("conv_subagent_abc"))

        # Bind conversation ID
        self.registry.bind_native_conversation("w_impl_01", "conv_subagent_abc")
        self.assertEqual(worker.native_session_id, "conv_subagent_abc")

        # Find by conversation ID
        found = self.registry.find_by_conversation_id("conv_subagent_abc")
        self.assertIsNotNone(found)
        self.assertEqual(found.worker_id, "w_impl_01")

        # Find by native agent name
        by_agent = self.registry.find_by_native_agent("implementer")
        self.assertEqual(len(by_agent), 1)
        self.assertEqual(by_agent[0].worker_id, "w_impl_01")

        # Mark session stale
        self.registry.mark_native_session_stale("w_impl_01")
        self.assertIsNone(worker.native_session_id)
        self.assertIsNone(self.registry.find_by_conversation_id("conv_subagent_abc"))

    def test_native_dispatch_fresh_worker_yields_invoke_subagent(self):
        """Fresh worker dispatch should generate invoke_subagent payload with canonical native agent name and model."""
        worker = Worker(
            worker_id="w_fe_01",
            domain="frontend",
            native_agent_name="implementer",
            is_native=True,
        )
        task = Task(
            task_id="task_ui",
            domain="frontend",
            description="Build dashboard component",
            write_set={"src/ui/dashboard.tsx"},
            read_set={"src/ui/types.ts"},
        )
        profile = ExecutionProfile(tier=ModelTier.PRO)

        self.adapter.dispatch(worker, task, profile)
        instruction = self.adapter.last_instruction
        self.assertIsInstance(instruction, NativeDispatchInstruction)
        self.assertTrue(instruction.is_first_launch)
        self.assertTrue(instruction.requires_subagent_invocation)
        self.assertFalse(instruction.requires_message_send)

        payload = instruction.to_invoke_subagent_payload()
        self.assertEqual(payload["TypeName"], "implementer")
        self.assertEqual(payload["Model"], "pro")
        self.assertIn("Build dashboard component", payload["Prompt"])
        self.assertIn("src/ui/dashboard.tsx", payload["Prompt"])

    def test_native_dispatch_warm_worker_yields_send_message(self):
        """Warm worker with bound conversation ID should generate send_message payload reusing the session."""
        worker = Worker(
            worker_id="w_fe_01",
            domain="frontend",
            native_agent_name="implementer",
            native_session_id="conv_existing_session_789",
            is_native=True,
        )
        task = Task(
            task_id="task_ui_followup",
            domain="frontend",
            description="Update dashboard buttons",
            write_set={"src/ui/dashboard.tsx"},
        )

        self.adapter.dispatch(worker, task)
        instruction = self.adapter.last_instruction
        self.assertIsInstance(instruction, NativeDispatchInstruction)
        self.assertFalse(instruction.is_first_launch)
        self.assertFalse(instruction.requires_subagent_invocation)
        self.assertTrue(instruction.requires_message_send)

        payload = instruction.to_send_message_payload()
        self.assertEqual(payload["Recipient"], "conv_existing_session_789")
        self.assertIn("task_ui_followup", payload["Message"])
        self.assertIn("Update dashboard buttons", payload["Message"])

    def test_native_repair_instruction_generation(self):
        """request_repair should produce a NativeRepairInstruction with failure context targeting the bound session."""
        worker = Worker(
            worker_id="w_be_01",
            domain="backend",
            native_agent_name="implementer",
            native_session_id="conv_be_123",
            is_native=True,
        )
        task = Task(
            task_id="task_service",
            domain="backend",
            description="Implement service handler",
            write_set={"src/service.py"},
        )
        repair_payload = {
            "attempt": 1,
            "error": "SyntaxError on line 42: invalid syntax",
            "tier": "TIER_1_SELF_TEST",
        }

        self.adapter.request_repair(worker, task, repair_payload)
        repair_instr = self.adapter.last_instruction
        self.assertIsInstance(repair_instr, NativeRepairInstruction)
        self.assertEqual(repair_instr.recipient_conversation_id, "conv_be_123")
        self.assertEqual(repair_instr.task_id, "task_service")
        self.assertEqual(repair_instr.attempt_number, 1)

        msg_payload = repair_instr.to_send_message_payload()
        self.assertEqual(msg_payload["Recipient"], "conv_be_123")
        self.assertIn("SyntaxError on line 42", msg_payload["Message"])

    def test_reuse_tracking_separation(self):
        """Adapter and registry should track internal reuse and native reuse distinctly."""
        worker = Worker(
            worker_id="w_test_01",
            domain="testing",
            native_agent_name="reviewer-verifier",
            is_native=True,
        )
        self.registry.register_worker(worker)

        # Initial dispatch without native conversation (internal dispatch simulation)
        task1 = Task(task_id="t1", domain="testing", description="test 1")
        self.adapter.dispatch(worker, task1)
        self.assertEqual(self.adapter.spawn_count, 1)
        self.assertEqual(self.adapter.internal_reuse_count, 0)
        self.assertEqual(self.adapter.native_reuse_count, 0)

        # Second dispatch without native session (internal reuse only)
        task2 = Task(task_id="t2", domain="testing", description="test 2")
        self.adapter.dispatch(worker, task2)
        self.assertEqual(self.adapter.internal_reuse_count, 1)
        self.assertEqual(self.adapter.native_reuse_count, 0)

        # Now bind native conversation ID
        self.registry.bind_native_conversation("w_test_01", "conv_session_test")
        task3 = Task(task_id="t3", domain="testing", description="test 3")
        self.adapter.dispatch(worker, task3)
        self.assertEqual(self.adapter.internal_reuse_count, 1)
        self.assertEqual(self.adapter.native_reuse_count, 1)

    def test_persistence_with_native_worker_state(self):
        """PersistenceManager should serialize native worker metadata and neutralize stale sessions on restore."""
        tmp_dir = tempfile.mkdtemp(prefix="ao_test_persistence_")
        ckpt_file = os.path.join(tmp_dir, "checkpoint.json")
        try:
            pm = PersistenceManager(state_file_path=ckpt_file)
            engine = MissionEngine(mission_id="m_native_test", title="Test Mission")

            worker = Worker(
                worker_id="w_persist_01",
                domain="backend",
                native_agent_name="implementer",
                native_session_id="conv_old_dead_session",
                model_tier=ModelTier.PRO,
                resolved_model="pro",
                is_native=True,
                is_reusable=True,
            )
            engine.workers.register_worker(worker)

            task = Task(task_id="t_persist", domain="backend", description="Persist me")
            engine.add_task(task)

            # Save mission state
            pm.save_mission(engine, file_path=ckpt_file)
            self.assertTrue(os.path.exists(ckpt_file))

            # Restore into clean engine
            restored_engine, report = pm.restore_mission_engine(file_path=ckpt_file)

            # Verify restored worker
            restored_worker = restored_engine.workers.get_worker("w_persist_01")
            self.assertIsNotNone(restored_worker)
            self.assertEqual(restored_worker.native_agent_name, "implementer")
            self.assertEqual(restored_worker.resolved_model, "pro")
            self.assertTrue(restored_worker.metadata.get("stale_session_detected", False))

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
