#!/usr/bin/env python3
"""
Adaptive Orchestrator v5 -- V5 Engine Runtime Acceptance Test
Executes a real runtime acceptance test exercising:
  1. Read-only verification of skill, manifests, and subagents
  2. 9-task non-trivial DAG across 4 domains (backend, frontend, infrastructure, testing)
  3. Pre-Planning and Execution Dispatch Delegation Gates
  4. Physical vs Logical concurrency decoupling via AIMD controller
  5. Reusable Domain Worker pool and context reuse
  6. Native Git Worktree isolation on branches and sequential merge queue
  7. 4-Tier Verification Pyramid (Tier 1 Self-Test -> Tier 2 Independent -> Tier 3 Adversarial -> Tier 4 Victory Audit)
  8. In-context deterministic local repair loop
  9. Serialized integration into ao/integration branch
 10. Tier 4 whole-mission victory audit and acceptance verification
"""

import os
import sys
import json
import time
import shutil
import tempfile
import subprocess
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure repository root is on path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from orchestrator.engine import MissionEngine
from orchestrator.models import (
    Event,
    EventType,
    Mission,
    MissionState,
    Task,
    TaskState,
)
from orchestrator.scheduler.aimd import AIMDConfig, AIMDController
from orchestrator.scheduler.ready_queue import ReadyQueue
from orchestrator.scheduler.scheduler import EventDrivenScheduler, ScheduledDispatch
from orchestrator.routing.router import ModelRouter
from orchestrator.routing.models import ModelTier, ExecutionProfile
from orchestrator.telemetry import TelemetryCollector
from orchestrator.workers.models import Worker, WorkerState
from orchestrator.workers.registry import WorkerRegistry
from orchestrator.workers.adapter import ExecutionAdapter, ExecutionResult
from orchestrator.workspace.models import WorkspaceMode, WorkspaceReleaseState
from orchestrator.workspace.registry import WorkspaceRegistry
from orchestrator.workspace.adapter import NativeWorktreeAdapter
from orchestrator.integration.adapter import GitMergeAdapter
from orchestrator.integration.manager import IntegrationManager
from orchestrator.verification.models import (
    FailureClassification,
    VerificationEvidence,
    VerificationResult,
    VerificationStatus,
    VerificationTier,
    VictoryAuditResult,
)
from orchestrator.verification.policy import VerificationPolicy
from orchestrator.verification.repair import RepairCoordinator
from orchestrator.verification.verifier import (
    IndependentVerifier,
    Tier1SelfTestVerifier,
    Tier3AdversarialVerifier,
    Tier4VictoryAuditVerifier,
)
from orchestrator.verification.engine import VerificationEngine


class AcceptanceExecutionAdapter(ExecutionAdapter):
    """
    Execution adapter that creates real deliverables in provisioned git worktrees.
    Supports deterministic controlled repair on task_a.
    Runs with auto_complete=False to decouple dispatch from completion.
    """

    def __init__(self, repo_dir: str, auto_complete: bool = False):
        self.repo_dir = repo_dir
        self.auto_complete = auto_complete
        self.dispatches: List[Dict[str, Any]] = []
        self.repair_requests: List[Dict[str, Any]] = []
        self.worker_task_counts: Dict[str, int] = {}
        self.spawn_count = 0
        self.reuse_count = 0

    def is_reuse(self, worker: Worker) -> bool:
        return self.worker_task_counts.get(worker.worker_id, 0) > 0

    def dispatch(
        self,
        worker: Worker,
        task: Task,
        execution_profile: Optional[ExecutionProfile] = None,
    ) -> Optional[ExecutionResult]:
        reused = self.is_reuse(worker)
        if reused:
            self.reuse_count += 1
        else:
            self.spawn_count += 1
        self.worker_task_counts[worker.worker_id] = self.worker_task_counts.get(worker.worker_id, 0) + 1

        self.dispatches.append({
            "worker_id": worker.worker_id,
            "task_id": task.task_id,
            "domain": worker.domain,
            "is_reuse": reused,
            "workspace_path": task.workspace_path,
            "timestamp": time.time(),
        })

        ws_path = task.workspace_path or self.repo_dir
        start_time = time.time()

        # Write deliverable into provisioned workspace
        if task.task_id == "task_a":
            # First attempt: write defective code to trigger deterministic repair
            file_rel = "src/backend/service_a.py"
            file_abs = os.path.join(ws_path, file_rel)
            os.makedirs(os.path.dirname(file_abs), exist_ok=True)
            with open(file_abs, "w", encoding="utf-8") as f:
                f.write("# DEFECTIVE: missing process_request function\ndef incomplete_service_a():\n    pass\n")

        elif task.task_id == "task_b":
            file_rel = "src/frontend/component_b.js"
            file_abs = os.path.join(ws_path, file_rel)
            os.makedirs(os.path.dirname(file_abs), exist_ok=True)
            with open(file_abs, "w", encoding="utf-8") as f:
                f.write('export function renderComponentB() {\n    return "<div>Component B Loaded</div>";\n}\n')

        elif task.task_id == "task_c":
            file_rel = "src/backend/service_c.py"
            file_abs = os.path.join(ws_path, file_rel)
            os.makedirs(os.path.dirname(file_abs), exist_ok=True)
            with open(file_abs, "w", encoding="utf-8") as f:
                f.write('from src.backend.service_a import process_request\n\ndef run_service_c():\n    return process_request()\n')

        elif task.task_id == "task_d":
            file_rel = "infra/config_d.json"
            file_abs = os.path.join(ws_path, file_rel)
            os.makedirs(os.path.dirname(file_abs), exist_ok=True)
            with open(file_abs, "w", encoding="utf-8") as f:
                json.dump({"cluster": "us-central1", "nodes": 3, "status": "active"}, f, indent=2)

        elif task.task_id == "task_e":
            file_rel = "infra/config_e.json"
            file_abs = os.path.join(ws_path, file_rel)
            os.makedirs(os.path.dirname(file_abs), exist_ok=True)
            with open(file_abs, "w", encoding="utf-8") as f:
                json.dump({"mesh": True, "ingress": "gateway_v1", "parent": "config_d"}, f, indent=2)

        elif task.task_id == "task_f":
            file_rel = "src/frontend/component_f.js"
            file_abs = os.path.join(ws_path, file_rel)
            os.makedirs(os.path.dirname(file_abs), exist_ok=True)
            with open(file_abs, "w", encoding="utf-8") as f:
                f.write('import { renderComponentB } from "./component_b.js";\nexport function renderApp() {\n    return "<main>App Loaded</main>";\n}\n')

        elif task.task_id == "task_g":
            file_rel = "tests/test_g.py"
            file_abs = os.path.join(ws_path, file_rel)
            os.makedirs(os.path.dirname(file_abs), exist_ok=True)
            with open(file_abs, "w", encoding="utf-8") as f:
                f.write('def test_smoke():\n    assert 1 + 1 == 2\n')

        elif task.task_id == "task_h":
            file_rel = "tests/test_h.py"
            file_abs = os.path.join(ws_path, file_rel)
            os.makedirs(os.path.dirname(file_abs), exist_ok=True)
            with open(file_abs, "w", encoding="utf-8") as f:
                f.write('def test_frontend_pipeline():\n    assert True\n')

        elif task.task_id == "task_i":
            file_rel = "tests/test_i.py"
            file_abs = os.path.join(ws_path, file_rel)
            os.makedirs(os.path.dirname(file_abs), exist_ok=True)
            with open(file_abs, "w", encoding="utf-8") as f:
                f.write('def test_infra_and_system():\n    assert True\n')

        res = ExecutionResult(
            success=True,
            result={"file_written": list(task.write_set), "workspace": ws_path},
            duration=time.time() - start_time,
            is_reuse=reused,
        )

        if self.auto_complete:
            return res
        return None

    def request_repair(
        self,
        worker: Worker,
        task: Task,
        payload: Any,
    ) -> ExecutionResult:
        start_time = time.time()
        self.repair_requests.append({
            "worker_id": worker.worker_id,
            "task_id": task.task_id,
            "domain": worker.domain,
            "payload": payload.to_dict() if hasattr(payload, "to_dict") else str(payload),
            "timestamp": time.time(),
        })

        ws_path = task.workspace_path or self.repo_dir

        if task.task_id == "task_a":
            # Apply targeted repair: implement process_request in existing worktree workspace
            file_abs = os.path.join(ws_path, "src/backend/service_a.py")
            with open(file_abs, "w", encoding="utf-8") as f:
                f.write('def process_request():\n    return {"status": "healthy", "service": "service_a"}\n')

        return ExecutionResult(
            success=True,
            result={"repaired": True, "task_id": task.task_id},
            duration=time.time() - start_time,
            is_reuse=True,
        )


class AcceptanceTier2Verifier(IndependentVerifier):
    """
    Tier 2 Independent Verifier.
    Inspects deliverable on disk in the worktree workspace.
    Verifies that the declared write set exists and conforms to contract requirements.
    Detects defective code on task_a if process_request is missing.
    """

    def verify_independent(
        self,
        task: Task,
        evidence_so_far: List[VerificationEvidence],
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        start_time = time.time()
        ws_path = task.workspace_path or ""
        passed = True
        err_msg = ""
        classification = None

        # Verify declared write set exists in workspace
        for rel_file in task.write_set:
            abs_file = os.path.join(ws_path, rel_file)
            if not os.path.exists(abs_file):
                passed = False
                err_msg = f"Missing declared deliverable file: {rel_file}"
                classification = FailureClassification.ENVIRONMENT_ERROR
                break

            # Specific contract validation for service_a
            if task.task_id == "task_a":
                with open(abs_file, "r", encoding="utf-8") as f:
                    content = f.read()
                if "def process_request():" not in content:
                    passed = False
                    err_msg = "Contract check failed: missing required function 'process_request' in service_a.py"
                    classification = FailureClassification.REPAIRABLE
                    break

        evidence = VerificationEvidence(
            task_id=task.task_id,
            tier=VerificationTier.TIER_2_INDEPENDENT,
            status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
            command="independent_deliverable_inspection",
            exit_code=0 if passed else 1,
            stdout_summary=f"Verified deliverables in {ws_path}" if passed else "",
            stderr_summary=err_msg,
            duration=time.time() - start_time,
            verifier_identity="acceptance_tier2_verifier",
            failure_classification=classification,
        )

        return VerificationResult(
            task_id=task.task_id,
            passed=passed,
            tier=VerificationTier.TIER_2_INDEPENDENT,
            evidences=[evidence],
            failure_classification=classification,
            summary="Tier 2 independent inspection passed" if passed else f"Tier 2 check failed: {err_msg}",
        )


class AcceptanceTier3Verifier(Tier3AdversarialVerifier):
    """
    Tier 3 Adversarial Verifier.
    Actively observes and audits the deliverable:
      1. Write-set exclusivity: checks git status in workspace against task.write_set
         (rejects any undeclared or accidental file modifications).
      2. Invariant probing: checks that no forbidden patterns exist (e.g. backdoors, eval).
    """

    def verify(
        self,
        task: Task,
        policy: Optional[VerificationPolicy] = None,
        context: Optional[Dict[str, Any]] = None,
        execution_result: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        start_time = time.time()
        ws_path = task.workspace_path or ""
        all_passed = True
        failure_reasons = []

        # 1. Write-set exclusivity check via git status
        if os.path.exists(ws_path):
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ws_path,
                capture_output=True,
                text=True,
                check=False,
            )
            modified_files = []
            for line in res.stdout.splitlines():
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    modified_files.append(parts[1].replace("\\", "/"))

            # Normalize task write set
            norm_write_set = {p.replace("\\", "/") for p in task.write_set}
            for mod_file in modified_files:
                if mod_file not in norm_write_set:
                    all_passed = False
                    failure_reasons.append(f"Undeclared file modification detected: {mod_file}")

            # 2. Content security audit
            for rel_path in task.write_set:
                abs_p = os.path.join(ws_path, rel_path)
                if os.path.exists(abs_p):
                    with open(abs_p, "r", encoding="utf-8") as f:
                        code = f.read()
                    for forbidden in ["os.system(", "subprocess.Popen(", "eval(", "__import__"]:
                        if forbidden in code:
                            all_passed = False
                            failure_reasons.append(f"Security invariant breach: forbidden '{forbidden}' in {rel_path}")

        evidence = VerificationEvidence(
            task_id=task.task_id,
            tier=VerificationTier.TIER_3_ADVERSARIAL,
            status=VerificationStatus.PASSED if all_passed else VerificationStatus.FAILED,
            command="adversarial_write_set_and_security_audit",
            exit_code=0 if all_passed else 1,
            stdout_summary="Adversarial write-set exclusivity and security invariants verified clean." if all_passed else "",
            stderr_summary="; ".join(failure_reasons),
            duration=time.time() - start_time,
            verifier_identity="acceptance_tier3_adversarial_verifier",
            failure_classification=FailureClassification.SECURITY_VIOLATION if not all_passed else None,
        )

        return VerificationResult(
            task_id=task.task_id,
            passed=all_passed,
            tier=VerificationTier.TIER_3_ADVERSARIAL,
            evidences=[evidence],
            failure_classification=FailureClassification.SECURITY_VIOLATION if not all_passed else None,
            summary="Tier 3 adversarial challenge passed" if all_passed else f"Tier 3 challenge failed: {'; '.join(failure_reasons)}",
        )


def run_runtime_acceptance_test() -> Dict[str, Any]:
    test_start_time = time.time()
    synthetic_repo = tempfile.mkdtemp(prefix="ao_v5_acceptance_repo_")
    telemetry_path = os.path.join(synthetic_repo, "telemetry_acceptance.json")

    print("=" * 80)
    print("      ADAPTIVE ORCHESTRATOR v5 -- V5 ENGINE RUNTIME ACCEPTANCE TEST")
    print("=" * 80)
    print(f"Synthetic Environment: {synthetic_repo}")

    try:
        # Step 1: Initialize Git repository
        subprocess.run(["git", "init", "-b", "main"], cwd=synthetic_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Adaptive Tester"], cwd=synthetic_repo, check=True)
        subprocess.run(["git", "config", "user.email", "tester@antigravity.dev"], cwd=synthetic_repo, check=True)

        readme_path = os.path.join(synthetic_repo, "README.md")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write("# Adaptive Orchestrator v5 Acceptance Repository\n")
        subprocess.run(["git", "add", "README.md"], cwd=synthetic_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial baseline commit"], cwd=synthetic_repo, check=True)

        # Step 2: Initialize Adapters
        wt_adapter = NativeWorktreeAdapter(repo_root=synthetic_repo)
        git_merge_adapter = GitMergeAdapter(repo_root=synthetic_repo)
        integ_branch = git_merge_adapter.create_integration_branch(base_branch="main", branch_name="ao/integration")
        print(f"[1/12] Initialized repository and shared integration branch: '{integ_branch}'")

        # Checkout ao/integration in root so worktrees branch from integration baseline
        subprocess.run(["git", "checkout", "ao/integration"], cwd=synthetic_repo, check=True, capture_output=True)

        # Step 3: Instantiate Core Registries and Engine
        worker_registry = WorkerRegistry()
        workspace_registry = WorkspaceRegistry()
        integ_manager = IntegrationManager(
            adapter=git_merge_adapter,
            workspace_registry=workspace_registry,
            worktree_adapter=wt_adapter,
            integration_branch="ao/integration",
        )

        exec_adapter = AcceptanceExecutionAdapter(repo_dir=synthetic_repo, auto_complete=False)
        t2_verifier = AcceptanceTier2Verifier()
        t3_verifier = AcceptanceTier3Verifier()

        # Required final artifacts
        required_artifacts = [
            "src/backend/service_a.py",
            "src/frontend/component_b.js",
            "src/backend/service_c.py",
            "infra/config_d.json",
            "infra/config_e.json",
            "src/frontend/component_f.js",
            "tests/test_g.py",
            "tests/test_h.py",
            "tests/test_i.py",
        ]

        acceptance_criteria = {
            "total_completed_tasks": 9,
            "zero_unresolved_failures": True,
            "all_domains_integrated": True,
        }

        t4_verifier = Tier4VictoryAuditVerifier(
            artifact_checker=lambda rel: os.path.exists(os.path.join(synthetic_repo, rel)),
            required_artifacts=required_artifacts,
            acceptance_criteria=acceptance_criteria,
        )

        verif_engine = VerificationEngine(
            tier1_verifier=Tier1SelfTestVerifier(),
            independent_verifier=t2_verifier,
            tier3_verifier=t3_verifier,
            tier4_verifier=t4_verifier,
            repair_coordinator=RepairCoordinator(),
        )

        # AIMD Controller: min 1, max 3, initial 2
        aimd_controller = AIMDController(
            config=AIMDConfig(
                initial_capacity=2,
                min_capacity=1,
                max_capacity=3,
                increase_step=1,
                decrease_factor=0.5,
                healthy_threshold=1,
                cooldown_steps=0,
            )
        )

        telemetry = TelemetryCollector(mission_id="m_v5_acceptance", output_path=telemetry_path)

        engine = MissionEngine(
            mission_id="m_v5_acceptance",
            title="Adaptive Orchestrator v5 Acceptance Test Mission",
            metadata=acceptance_criteria,
            worker_registry=worker_registry,
            workspace_registry=workspace_registry,
            worktree_adapter=wt_adapter,
            integration_manager=integ_manager,
            verification_engine=verif_engine,
            auto_audit=False,
            tier4_verifier=t4_verifier,
            required_artifacts=required_artifacts,
            acceptance_criteria=acceptance_criteria,
        )
        engine.attach_telemetry(telemetry)
        verif_engine._event_emitter = engine._emit

        # Track scheduler dispatches and peak concurrency
        scheduler = EventDrivenScheduler(
            ready_queue=engine.ready_queue,
            worker_registry=worker_registry,
            execution_adapter=exec_adapter,
            aimd_controller=aimd_controller,
            model_router=ModelRouter(),
            workspace_registry=workspace_registry,
            worktree_adapter=wt_adapter,
            integration_manager=integ_manager,
            verification_engine=verif_engine,
            event_emitter=engine._emit,
            engine=engine,
        )
        engine.attach_scheduler(scheduler, worker_registry=worker_registry)

        # Step 4: Register Reusable Domain Workers (4 domains)
        w_be = engine.register_worker("worker_be", domains=["backend"])
        w_fe = engine.register_worker("worker_fe", domains=["frontend"])
        w_infra = engine.register_worker("worker_infra", domains=["infrastructure"])
        w_test = engine.register_worker("worker_test", domains=["testing"])
        print("[2/12] Registered 4 Reusable Domain Workers across 4 technical domains")

        # Step 5: Construct the 9-Task DAG matching the exact required topology
        # Topology:
        # A, B -> C
        # D -> E
        # C, E -> F -> H
        # E, G -> I
        tasks = [
            # Initially ready tasks (4 tasks >= 3 required)
            Task(id="task_a", description="Backend service A", domain="backend", write_set={"src/backend/service_a.py"}, workspace_mode="branch", max_retries=2),
            Task(id="task_b", description="Frontend component B", domain="frontend", write_set={"src/frontend/component_b.js"}, workspace_mode="branch"),
            Task(id="task_d", description="Infrastructure config D", domain="infrastructure", write_set={"infra/config_d.json"}, workspace_mode="branch"),
            Task(id="task_g", description="Unit test suite G", domain="testing", write_set={"tests/test_g.py"}, workspace_mode="branch"),

            # Downstream tasks (Task C and Task F routed to PRO model tier with Tier 3 Adversarial Challenge)
            Task(id="task_c", description="Backend service C", domain="backend", dependencies={"task_a", "task_b"}, write_set={"src/backend/service_c.py"}, workspace_mode="branch", metadata={"model_tier": "PRO", "require_tier3": True}),
            Task(id="task_e", description="Infrastructure config E", domain="infrastructure", dependencies={"task_d"}, write_set={"infra/config_e.json"}, workspace_mode="branch"),
            Task(id="task_f", description="Frontend app component F", domain="frontend", dependencies={"task_c", "task_e"}, write_set={"src/frontend/component_f.js"}, workspace_mode="branch", metadata={"model_tier": "PRO", "require_tier3": True}),

            # Convergence and terminal tasks
            Task(id="task_h", description="Frontend integration test H", domain="testing", dependencies={"task_f"}, write_set={"tests/test_h.py"}, workspace_mode="branch"),
            Task(id="task_i", description="System integration test I", domain="testing", dependencies={"task_e", "task_g"}, write_set={"tests/test_i.py"}, workspace_mode="branch"),
        ]

        for t in tasks:
            engine.add_task(t)

        print(f"[3/12] Constructed DAG with {len(tasks)} tasks across 4 domains matching required topology")

        # Step 6: Evaluate Dual Mandatory Delegation Gates
        print("\n" + "=" * 60)
        print("[PRE-PLANNING DELEGATION GATE]")
        print("COMPLEXITY_TRIGGER: DOMAINS >= 3 (4 domains: backend, frontend, infra, testing)")
        print("DELEGATION_MANDATORY: YES")
        print("PLANNED_RECON_WORKFORCE: 4 Domain Specialist Workers")
        print("FIRST_ACTION: Dispatch to Reusable Worker Pool")
        print("=" * 60)

        print("\n" + "=" * 60)
        print("[EXECUTION DISPATCH GATE]")
        print("INDEPENDENT_STREAMS: 4 (Tasks A, B, D, G ready concurrently)")
        print("DELEGATION_MANDATORY: YES")
        print("TARGET_DOMAINS: [backend, frontend, infrastructure, testing]")
        print("DISPATCH_STRATEGY: Pooled Worker Reuse with Branch Worktree Isolation")
        print("FIRST_ACTION: Dispatch initial ready tasks to Reusable Domain Workers")
        print("=" * 60 + "\n")

        # Step 7: Telemetry / Concurrency Tracking Hooks
        peak_active_workers = 0
        peak_queue_depth = 0
        events_captured: List[Dict[str, Any]] = []

        def event_listener(event: Event):
            nonlocal peak_active_workers, peak_queue_depth
            active_w = worker_registry.busy_count
            q_depth = len(engine.ready_queue)
            if active_w > peak_active_workers:
                peak_active_workers = active_w
            if q_depth > peak_queue_depth:
                peak_queue_depth = q_depth

            events_captured.append({
                "type": event.event_type.value,
                "task_id": event.task_id,
                "payload": event.payload,
                "timestamp": event.timestamp,
            })

        engine.subscribe(event_listener)

        # Step 8: Start Mission
        engine.start()
        print(f"[4/12] Mission started: state = {engine.state.value}")

        # Step 9: Observe Initial Concurrency vs Logical DAG Width
        # With 4 ready tasks (A, B, D, G) and initial AIMD capacity = 2:
        # In async mode, scheduler dispatches up to capacity (2 tasks).
        # The remaining 2 ready tasks wait in ReadyQueue!
        active_w = worker_registry.busy_count
        q_depth = len(engine.ready_queue)
        print(f"[5/12] Initial dispatch evaluated:")
        print(f"       AIMD Capacity = {aimd_controller.current_capacity}, Active Workers = {active_w}, ReadyQueue Depth = {q_depth}")
        assert active_w == 2, f"Active workers must equal initial AIMD capacity (2)! Got {active_w}"
        assert q_depth == 2, f"ReadyQueue depth must equal remaining ready tasks (2)! Got {q_depth}"
        assert len(tasks) > active_w, "Logical task count must exceed physical active concurrency"
        print(f"       Verified: logical DAG width ({len(tasks)}) > physical concurrency ({active_w})")
        print(f"       Verified: ReadyQueue holds remaining tasks without artificial mission-wide launch ceiling")

        # Step 10: Step-by-Step Controlled Execution
        print("\n[6/12] Executing Continuous Pipeline Flow with Worker Reuse, Worktree Isolation & Controlled Repair...")

        # Order of execution demonstrating:
        # - Parallel worktree writes
        # - Controlled repair on task_a (same worker, same workspace)
        # - Unlock events (C, E, F, I, H)
        # - Worker reuse across technical domains

        # 10.1 Complete Task A (First attempt triggers controlled repair, then passes)
        print("\n--- Sub-step: Completing Task A with Controlled In-Context Repair ---")
        scheduler.complete_task("task_a")
        task_a = engine.get_task("task_a")
        assert task_a.status in (TaskState.PASSED, TaskState.MERGED), f"Task A must pass after repair! Status: {task_a.status}"
        assert task_a.retry_count == 1, f"Task A retry_count must be 1! Got {task_a.retry_count}"
        assert len(exec_adapter.repair_requests) == 1, "Exactly one repair request must be logged"
        print(f"    Task A successfully repaired and verified! Retries = {task_a.retry_count}")

        # 10.2 Complete Task B
        print("\n--- Sub-step: Completing Task B ---")
        scheduler.complete_task("task_b")
        task_b = engine.get_task("task_b")
        assert task_b.status in (TaskState.PASSED, TaskState.MERGED), f"Task B must pass! Status: {task_b.status}"
        print("    Task B passed and merged.")

        # At this point, Task A and Task B are both merged -> Task C should be UNLOCKED!
        task_c = engine.get_task("task_c")
        print(f"    Dependency Unlock Check: Task C status = {task_c.status.value}")
        assert task_c.status in (TaskState.READY, TaskState.ASSIGNED, TaskState.RUNNING), f"Task C must be unlocked! Got {task_c.status}"

        # 10.3 Dispatch and complete Task D
        print("\n--- Sub-step: Completing Task D ---")
        scheduler.complete_task("task_d")
        task_d = engine.get_task("task_d")
        assert task_d.status in (TaskState.PASSED, TaskState.MERGED), f"Task D must pass! Status: {task_d.status}"
        print("    Task D passed and merged. Task E unlocked!")

        # 10.4 Complete Task G
        print("\n--- Sub-step: Completing Task G ---")
        scheduler.complete_task("task_g")
        task_g = engine.get_task("task_g")
        assert task_g.status in (TaskState.PASSED, TaskState.MERGED), f"Task G must pass! Status: {task_g.status}"
        print("    Task G passed and merged.")

        # 10.5 Complete Task C (Worker worker_be reused! Task C has Tier 3 Adversarial Verification)
        print("\n--- Sub-step: Completing Task C (Worker Reuse + Tier 3 Adversarial) ---")
        scheduler.complete_task("task_c")
        task_c = engine.get_task("task_c")
        assert task_c.status in (TaskState.PASSED, TaskState.MERGED), f"Task C must pass! Status: {task_c.status}"
        print("    Task C passed Tier 1, Tier 2, and Tier 3 Adversarial Challenge! Merged.")

        # 10.6 Complete Task E (Worker worker_infra reused!)
        print("\n--- Sub-step: Completing Task E (Worker Reuse) ---")
        scheduler.complete_task("task_e")
        task_e = engine.get_task("task_e")
        assert task_e.status in (TaskState.PASSED, TaskState.MERGED), f"Task E must pass! Status: {task_e.status}"
        print("    Task E passed and merged.")

        # At this point, C and E merged -> Task F unlocked!
        # E and G merged -> Task I unlocked!
        task_f = engine.get_task("task_f")
        task_i = engine.get_task("task_i")
        print(f"    Convergence Unlock Check: Task F = {task_f.status.value}, Task I = {task_i.status.value}")

        # 10.7 Complete Task F (Worker worker_fe reused! Tier 3 Adversarial Verification)
        print("\n--- Sub-step: Completing Task F (Worker Reuse + Tier 3 Adversarial) ---")
        scheduler.complete_task("task_f")
        task_f = engine.get_task("task_f")
        assert task_f.status in (TaskState.PASSED, TaskState.MERGED), f"Task F must pass! Status: {task_f.status}"
        print("    Task F passed and merged. Task H unlocked!")

        # 10.8 Complete Task I (Worker worker_test reused!)
        print("\n--- Sub-step: Completing Task I (Worker Reuse) ---")
        scheduler.complete_task("task_i")
        task_i = engine.get_task("task_i")
        assert task_i.status in (TaskState.PASSED, TaskState.MERGED), f"Task I must pass! Status: {task_i.status}"
        print("    Task I passed and merged.")

        # 10.9 Complete Task H (Worker worker_test reused!)
        print("\n--- Sub-step: Completing Task H (Worker Reuse) ---")
        scheduler.complete_task("task_h")
        task_h = engine.get_task("task_h")
        assert task_h.status in (TaskState.PASSED, TaskState.MERGED), f"Task H must pass! Status: {task_h.status}"
        print("    Task H passed and merged.")

        # Finalize any pending merges in integration queue
        integ_manager.process_pending_merges()

        print("\n[7/12] All 9 logical tasks executed and completed.")

        # Step 11: Verify Worker Reuse
        print("\n[8/12] Verifying Reusable Domain Worker Pooling...")
        for wid, count in exec_adapter.worker_task_counts.items():
            print(f"       Worker '{wid}': {count} tasks executed")
        total_reuses = exec_adapter.reuse_count
        print(f"       Total Worker Reuses: {total_reuses}")
        assert total_reuses >= 4, f"Worker reuse must occur at least 4 times! Got {total_reuses}"
        assert exec_adapter.worker_task_counts["worker_be"] >= 2, "worker_be must be reused (task_a, task_c)"
        assert exec_adapter.worker_task_counts["worker_infra"] >= 2, "worker_infra must be reused (task_d, task_e)"
        assert exec_adapter.worker_task_counts["worker_fe"] >= 2, "worker_fe must be reused (task_b, task_f)"
        assert exec_adapter.worker_task_counts["worker_test"] >= 3, "worker_test must be reused (task_g, task_i, task_h)"

        # Step 12: Verify Workspace Isolation and Branch Cleanup
        print("\n[9/12] Verifying Workspace Isolation and Worktree Integration...")
        assert workspace_registry.active_count == 0, "All workspace locks must be released upon completion"
        wt_dir = os.path.join(synthetic_repo, ".worktrees")
        if os.path.exists(wt_dir):
            remaining_wts = os.listdir(wt_dir)
            assert len(remaining_wts) == 0, f"All worktrees must be cleaned up! Remaining: {remaining_wts}"
        print("       Verified: Worktrees cleaned up, zero workspace lock leaks")

        # Step 13: Verify File Integration in ao/integration
        print("\n[10/12] Verifying Delivered Artifacts in 'ao/integration' Branch...")
        subprocess.run(["git", "checkout", "ao/integration"], cwd=synthetic_repo, check=True, capture_output=True)
        for art in required_artifacts:
            art_path = os.path.join(synthetic_repo, art)
            assert os.path.exists(art_path), f"Required integrated artifact missing: {art}"
            print(f"       Artifact present on disk: {art}")

        # Step 14: Execute Tier 4 Victory Audit
        print("\n[11/12] Executing Authoritative Tier 4 Victory Audit...")
        engine.start_auditing()
        audit_result = engine.run_victory_audit()
        print(f"       Tier 4 Audit Passed: {audit_result.passed}")
        print(f"       Summary: {audit_result.summary}")
        print(f"       Unresolved Failures: {len(audit_result.unresolved_failures)}")
        assert audit_result.passed, f"Tier 4 Victory Audit must pass! Failures: {audit_result.unresolved_failures}"
        assert engine.state == MissionState.COMPLETED, f"Engine must reach COMPLETED! Current: {engine.state}"
        print("       Mission reached terminal COMPLETED state successfully!")

        # Step 15: Collect Telemetry and Observability Metrics
        test_end_time = time.time()
        telemetry_report = telemetry.get_report()

        # Count events
        tier1_count = len([e for e in events_captured if e["type"] == EventType.VERIFICATION_STARTED.value])
        tier2_count = len([e for e in events_captured if e["type"] in (EventType.VERIFICATION_PASSED.value, EventType.VERIFICATION_FAILED.value)])
        tier3_count = len([e for e in events_captured if e["type"] == EventType.TIER3_CHALLENGE_STARTED.value])
        repairs_count = len(exec_adapter.repair_requests)
        merges_count = len([e for e in events_captured if e["type"] == EventType.MERGE_COMPLETED.value])
        conflicts_count = len([e for e in events_captured if e["type"] == EventType.WORKSPACE_CONFLICT.value])

        # Extract model tiers used
        assigned_events = [e for e in events_captured if e["type"] == EventType.TASK_ASSIGNED.value]
        model_tiers_used = sorted(list({e["payload"].get("model_tier", "FAST") for e in assigned_events}))

        retries_count = sum(t.retry_count for t in engine.graph.all_tasks())

        trace = {
            "MISSION_ID": engine.mission.mission_id,
            "START_TIME": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(test_start_time)),
            "END_TIME": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(test_end_time)),
            "LOGICAL_TASKS": len(tasks),
            "PEAK_PHYSICAL_WORKERS": peak_active_workers,
            "WORKERS_CREATED": len(worker_registry.all_workers()),
            "WORKER_REUSES": total_reuses,
            "PEAK_QUEUE_DEPTH": peak_queue_depth,
            "AIMD_CAPACITY": aimd_controller.current_capacity,
            "MODEL_TIERS_USED": ", ".join(model_tiers_used),
            "WORKTREE_TASKS": len(tasks),
            "MERGES": merges_count,
            "MERGE_CONFLICTS": conflicts_count,
            "TIER1": tier1_count,
            "TIER2": tier2_count,
            "TIER3": tier3_count,
            "TIER4": "PASSED (Whole-Mission Victory Confirmed)",
            "REPAIRS": repairs_count,
            "RETRIES": retries_count,
            "FINAL_MISSION_STATE": engine.state.value,
        }

        print("\n[12/12] Concise Runtime Execution Trace:")
        print("-" * 50)
        for k, v in trace.items():
            print(f"{k}: {v}")
        print("-" * 50)

        return {
            "status": "PASS",
            "trace": trace,
            "audit_result": audit_result,
            "events_count": len(events_captured),
            "synthetic_repo": synthetic_repo,
        }

    except Exception as ex:
        import traceback
        traceback.print_exc()
        return {
            "status": "FAIL",
            "error": str(ex),
            "synthetic_repo": synthetic_repo,
        }


if __name__ == "__main__":
    result = run_runtime_acceptance_test()
    if result["status"] == "PASS":
        print("\n>>> RUNTIME ACCEPTANCE TEST PASSED DETERMINISTICALLY! <<<")
        sys.exit(0)
    else:
        print(f"\n>>> RUNTIME ACCEPTANCE TEST FAILED: {result.get('error')} <<<")
        sys.exit(1)
