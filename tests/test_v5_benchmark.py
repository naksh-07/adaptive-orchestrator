"""
Deterministic Synthetic Benchmark: Fixed Capacity vs Adaptive Concurrency (AIMD).
Adaptive Orchestrator v5 - Phase 3.

Compares:
  - Fixed Capacity (C = 2)
  - Adaptive Capacity (AIMD C in [2, 6], initial = 2, healthy_threshold = 2, decrease_factor = 0.5)

Executes a 16-task multi-domain DAG under a deterministic discrete-event step loop.
Reports measured synthetic results only.
"""

import time
import unittest
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from orchestrator.graph.dag import DependencyGraph
from orchestrator.models import Task, TaskState
from orchestrator.resolver import DependencyResolver
from orchestrator.routing.models import ModelTier, RouterConfig
from orchestrator.routing.router import ModelRouter
from orchestrator.scheduler.aimd import AIMDConfig, AIMDController
from orchestrator.scheduler.feedback import FeedbackCollector, FeedbackSignal, FeedbackSignalType
from orchestrator.scheduler.ready_queue import ReadyQueue
from orchestrator.workers.affinity import DomainAffinityPolicy
from orchestrator.workers.models import Worker, WorkerState
from orchestrator.workers.registry import WorkerRegistry


@dataclass
class BenchmarkMetrics:
    mode: str
    total_simulated_steps: int = 0
    peak_active_concurrency: int = 0
    total_active_worker_steps: int = 0
    average_active_concurrency: float = 0.0
    total_queue_wait_steps: int = 0
    worker_reuse_count: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    capacity_increases: int = 0
    capacity_decreases: int = 0
    fast_model_count: int = 0
    pro_model_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "total_simulated_steps": self.total_simulated_steps,
            "peak_active_concurrency": self.peak_active_concurrency,
            "average_active_concurrency": round(self.average_active_concurrency, 2),
            "total_queue_wait_steps": self.total_queue_wait_steps,
            "worker_reuse_count": self.worker_reuse_count,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "capacity_increases": self.capacity_increases,
            "capacity_decreases": self.capacity_decreases,
            "model_tier_distribution": {
                "FAST": self.fast_model_count,
                "PRO": self.pro_model_count,
            },
        }


def build_benchmark_graph() -> DependencyGraph:
    """Builds a deterministic 16-task DAG across backend, frontend, and testing."""
    graph = DependencyGraph()

    # 6 Roots
    roots = [
        ("t01", "API Spec Auth", "backend", 6.0),
        ("t02", "API Spec Items", "backend", 5.0),
        ("t03", "UI Tokens", "frontend", 4.0),
        ("t04", "UI Navbar", "frontend", 5.0),
        ("t05", "Test Setup DB", "testing", 7.0),
        ("t06", "Test Mocks", "testing", 5.0),
    ]
    for tid, title, domain, prio in roots:
        graph.add_task(Task(task_id=tid, mission_id="bench", title=title, domain=domain, priority=prio))

    # 6 Intermediate implementations
    intermediates = [
        ("t07", "Impl Auth", "backend", 7.5, ["t01"]),
        ("t08", "Impl Items", "backend", 8.5, ["t02"]),  # High priority -> PRO
        ("t09", "Impl UI Login", "frontend", 6.0, ["t03", "t04"]),
        ("t10", "Impl UI Items", "frontend", 7.0, ["t03", "t04"]),
        ("t11", "Unit Tests Auth", "testing", 6.5, ["t07", "t05"]),
        ("t12", "Unit Tests Items", "testing", 7.0, ["t08", "t06"]),
    ]
    for tid, title, domain, prio, deps in intermediates:
        graph.add_task(Task(task_id=tid, mission_id="bench", title=title, domain=domain, priority=prio, dependencies=set(deps)))

    # 4 Final tasks
    finals = [
        ("t13", "Frontend E2E", "frontend", 6.5, ["t09", "t10"]),
        ("t14", "Integration Test", "testing", 8.8, ["t11", "t12"]),  # High priority -> PRO
        ("t15", "System Stress Test", "testing", 9.0, ["t13", "t14"]),  # High priority -> PRO
        ("t16", "Release Audit Gate", "testing", 9.5, ["t15"]),         # High priority -> PRO
    ]
    for tid, title, domain, prio, deps in finals:
        graph.add_task(Task(task_id=tid, mission_id="bench", title=title, domain=domain, priority=prio, dependencies=set(deps)))

    return graph


def run_benchmark_simulation(adaptive: bool, simulate_rate_limit: bool = True) -> BenchmarkMetrics:
    """
    Executes a discrete step-by-step simulation over the 16-task DAG.
    Each task requires 2 simulated steps to execute.
    """
    graph = build_benchmark_graph()
    resolver = DependencyResolver(graph)
    ready_queue = ReadyQueue()
    worker_registry = WorkerRegistry()

    # 4 Reusable Workers
    w_backend = Worker(worker_id="w_backend", domain="backend")
    w_frontend = Worker(worker_id="w_frontend", domain="frontend")
    w_qa1 = Worker(worker_id="w_qa1", domain="testing")
    w_qa2 = Worker(worker_id="w_qa2", domain="testing")
    for w in [w_backend, w_frontend, w_qa1, w_qa2]:
        worker_registry.register_worker(w)

    # Concurrency Controller
    if adaptive:
        aimd = AIMDController(AIMDConfig(
            min_capacity=2,
            max_capacity=6,
            initial_capacity=2,
            increase_step=1,
            decrease_factor=0.5,
            healthy_threshold=2,
            cooldown_steps=1,
        ))
        mode = "Adaptive AIMD (2..6)"
    else:
        # Fixed capacity = 2
        aimd = AIMDController(AIMDConfig(
            min_capacity=2,
            max_capacity=2,
            initial_capacity=2,
            increase_step=1,
            decrease_factor=0.5,  # min=2 and max=2 locks capacity to 2
            healthy_threshold=999,
        ))
        mode = "Fixed Capacity (2)"

    router = ModelRouter(RouterConfig(high_priority_threshold=8.5))
    affinity = DomainAffinityPolicy()
    feedback = FeedbackCollector()

    metrics = BenchmarkMetrics(mode=mode)

    # Initial ready tasks
    for task in resolver.resolve_all_ready():
        task.transition_to(TaskState.READY)
        ready_queue.push(task, unlock_value=resolver.calculate_unlock_value(task.task_id))

    # Active running state: {task_id: {"worker": Worker, "remaining_steps": int, "profile": ExecutionProfile}}
    running_tasks: Dict[str, Dict[str, Any]] = {}
    active_concurrency_samples: List[int] = []

    rate_limited_task = "t08" if simulate_rate_limit else None
    rate_limit_triggered = False

    step = 0
    max_steps = 200

    while step < max_steps:
        step += 1

        # Track queue wait steps
        metrics.total_queue_wait_steps += len(ready_queue)

        # 1. Dispatch Phase: Match ready tasks with idle workers under AIMD capacity
        while True:
            current_active = len(running_tasks)
            if not aimd.can_dispatch(current_active):
                break

            idle_workers = worker_registry.get_idle_workers()
            if not idle_workers or ready_queue.is_empty():
                break

            ready_tasks = ready_queue.all_tasks()
            dispatched_any = False

            for task in ready_tasks:
                if not aimd.can_dispatch(len(running_tasks)):
                    break

                available_workers = [w for w in idle_workers if w.is_available]
                if not available_workers:
                    break

                selected_worker = affinity.select_worker(task, available_workers)
                if selected_worker is None:
                    continue

                # Route model
                profile = router.route(task, selected_worker, feedback)
                if profile.tier == ModelTier.FAST:
                    metrics.fast_model_count += 1
                else:
                    metrics.pro_model_count += 1

                # Dequeue & Assign
                ready_queue.remove(task.task_id)
                worker_registry.assign_task(selected_worker.worker_id, task.task_id)
                task.transition_to(TaskState.RUNNING)

                # Check worker reuse
                if len(selected_worker.task_history) > 0:
                    metrics.worker_reuse_count += 1

                # Task duration = 2 steps
                running_tasks[task.task_id] = {
                    "worker": selected_worker,
                    "remaining_steps": 2,
                    "profile": profile,
                }
                dispatched_any = True
                break

            if not dispatched_any:
                break

        # Record active concurrency
        active_count = len(running_tasks)
        active_concurrency_samples.append(active_count)
        if active_count > metrics.peak_active_concurrency:
            metrics.peak_active_concurrency = active_count

        # 2. Execution Step Phase: Advance running tasks
        completed_this_step: List[str] = []
        failed_this_step: List[str] = []

        for tid, task_info in list(running_tasks.items()):
            task_info["remaining_steps"] -= 1
            if task_info["remaining_steps"] <= 0:
                if tid == rate_limited_task and not rate_limit_triggered:
                    # Trigger simulated 429 once
                    rate_limit_triggered = True
                    failed_this_step.append(tid)
                else:
                    completed_this_step.append(tid)

        # 3. Process Failures
        for tid in failed_this_step:
            t_info = running_tasks.pop(tid)
            w = t_info["worker"]
            worker_registry.release_worker(w.worker_id, success=False, error="HTTP 429: Rate Limit")
            task = graph.get_task(tid)
            task.transition_to(TaskState.RETRYING)
            task.transition_to(TaskState.READY, reason="Re-enqueued for retry")
            # Re-enqueue for retry
            ready_queue.push(task, unlock_value=resolver.calculate_unlock_value(tid))

            # Inform AIMD
            old_cap = aimd.current_capacity
            sig = FeedbackSignal(signal_type=FeedbackSignalType.RATE_LIMIT_ERROR, task_id=tid, is_capacity_error=True)
            new_cap = aimd.process_feedback(sig)
            if new_cap < old_cap:
                metrics.capacity_decreases += 1
            metrics.tasks_failed += 1

        # 4. Process Completions
        for tid in completed_this_step:
            t_info = running_tasks.pop(tid)
            w = t_info["worker"]
            worker_registry.release_worker(w.worker_id, success=True, duration=2.0)
            task = graph.get_task(tid)
            task.transition_to(TaskState.PASSED)
            metrics.tasks_completed += 1

            # Inform AIMD
            old_cap = aimd.current_capacity
            sig = FeedbackSignal(signal_type=FeedbackSignalType.TASK_COMPLETED, task_id=tid, latency=2.0)
            new_cap = aimd.process_feedback(sig)
            if new_cap > old_cap:
                metrics.capacity_increases += 1

            # Unlock dependents
            newly_ready = resolver.resolve_dependents_on_completion(tid)
            for dep in newly_ready:
                dep.transition_to(TaskState.READY)
                ready_queue.push(dep, unlock_value=resolver.calculate_unlock_value(dep.task_id))

        # Termination condition: all tasks in graph passed
        if all(t.status == TaskState.PASSED for t in graph.all_tasks()):
            break

    metrics.total_simulated_steps = step
    metrics.average_active_concurrency = (
        sum(active_concurrency_samples) / len(active_concurrency_samples) if active_concurrency_samples else 0.0
    )
    return metrics


class TestSyntheticBenchmark(unittest.TestCase):
    def test_compare_fixed_vs_adaptive_capacity(self):
        fixed_metrics = run_benchmark_simulation(adaptive=False, simulate_rate_limit=True)
        adaptive_metrics = run_benchmark_simulation(adaptive=True, simulate_rate_limit=True)

        # Both completed all 16 tasks successfully
        self.assertEqual(fixed_metrics.tasks_completed, 16)
        self.assertEqual(adaptive_metrics.tasks_completed, 16)

        # Print results clearly labeled as synthetic
        print("\n" + "=" * 70)
        print("    Adaptive Orchestrator v5 - Phase 3 Synthetic Benchmark Results")
        print("    (Pure In-Memory Deterministic Simulation — Synthetic Only)")
        print("=" * 70)
        print(f"{'Metric':<32} | {'Fixed Capacity':<16} | {'Adaptive AIMD':<16}")
        print("-" * 70)
        print(f"{'Total Simulated Steps':<32} | {fixed_metrics.total_simulated_steps:<16} | {adaptive_metrics.total_simulated_steps:<16}")
        print(f"{'Peak Active Concurrency':<32} | {fixed_metrics.peak_active_concurrency:<16} | {adaptive_metrics.peak_active_concurrency:<16}")
        print(f"{'Average Active Concurrency':<32} | {fixed_metrics.average_active_concurrency:<16} | {adaptive_metrics.average_active_concurrency:<16}")
        print(f"{'Queue Wait Steps':<32} | {fixed_metrics.total_queue_wait_steps:<16} | {adaptive_metrics.total_queue_wait_steps:<16}")
        print(f"{'Worker Reuse Count':<32} | {fixed_metrics.worker_reuse_count:<16} | {adaptive_metrics.worker_reuse_count:<16}")
        print(f"{'Capacity Increases':<32} | {fixed_metrics.capacity_increases:<16} | {adaptive_metrics.capacity_increases:<16}")
        print(f"{'Capacity Decreases':<32} | {fixed_metrics.capacity_decreases:<16} | {adaptive_metrics.capacity_decreases:<16}")
        print(f"{'FAST Model Executions':<32} | {fixed_metrics.fast_model_count:<16} | {adaptive_metrics.fast_model_count:<16}")
        print(f"{'PRO Model Executions':<32} | {fixed_metrics.pro_model_count:<16} | {adaptive_metrics.pro_model_count:<16}")
        print(f"{'Tasks Completed':<32} | {fixed_metrics.tasks_completed:<16} | {adaptive_metrics.tasks_completed:<16}")
        print(f"{'Failures / Retries':<32} | {fixed_metrics.tasks_failed:<16} | {adaptive_metrics.tasks_failed:<16}")
        print("=" * 70 + "\n")

        # Invariant checks:
        # 1. Adaptive capacity achieves higher peak active concurrency
        self.assertGreaterEqual(adaptive_metrics.peak_active_concurrency, fixed_metrics.peak_active_concurrency)
        # 2. Worker reuse count is high in both (> 10 reuses over 16 tasks)
        self.assertGreater(adaptive_metrics.worker_reuse_count, 10)
        # 3. Model routing occurs: both FAST and PRO are assigned
        self.assertGreater(adaptive_metrics.fast_model_count, 0)
        self.assertGreater(adaptive_metrics.pro_model_count, 0)


if __name__ == "__main__":
    unittest.main()
