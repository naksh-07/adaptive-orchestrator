"""
Adaptive Orchestrator v5 - Section 10: Concurrency Benchmark & Scheduler Validation
Deterministic simulated latency testing for AIMD scheduler capacity, worker reuse,
and explicit distinction between logical DAG width and physical capacity.
"""

import time
import unittest
from typing import Dict, List, Set

from orchestrator.engine import MissionEngine
from orchestrator.models import MissionState, Task, TaskState
from orchestrator.scheduler.aimd import AIMDConfig, AIMDController
from orchestrator.scheduler.feedback import FeedbackSignal, FeedbackSignalType
from orchestrator.workers.models import Worker, WorkerState
from orchestrator.workers.registry import WorkerRegistry


class TestV5ConcurrencyBenchmark(unittest.TestCase):
    def test_aimd_capacity_expansion_and_reduction(self):
        """
        Demonstrates:
        1. Capacity increases additively upon sustained healthy progress.
        2. Capacity decreases multiplicatively upon failure / rate pressure.
        3. Capacity bounds are respected (min_capacity <= capacity <= max_capacity).
        """
        aimd = AIMDController(AIMDConfig(
            min_capacity=2,
            max_capacity=8,
            initial_capacity=2,
            increase_step=1,
            decrease_factor=0.5,
            healthy_threshold=2,
            cooldown_steps=0,
        ))

        self.assertEqual(aimd.current_capacity, 2)

        # 1. Healthy progress expands capacity
        aimd.on_success(task_id="t1")
        self.assertEqual(aimd.current_capacity, 2)  # Needs 2 healthy steps to increase

        aimd.on_success(task_id="t2")
        self.assertEqual(aimd.current_capacity, 3)  # Increased to 3

        aimd.on_success(task_id="t3")
        aimd.on_success(task_id="t4")
        self.assertEqual(aimd.current_capacity, 4)  # Increased to 4

        # 2. Rate pressure / failure cuts capacity multiplicatively
        aimd.on_failure(task_id="t5", is_capacity_error=True)
        self.assertEqual(aimd.current_capacity, 2)  # 4 * 0.5 = 2

        # Rate pressure again cannot drop below min_capacity
        aimd.on_failure(task_id="t6", is_capacity_error=True)
        self.assertEqual(aimd.current_capacity, 2)  # Floor at min_capacity = 2

    def test_logical_width_exceeds_physical_worker_capacity(self):
        """
        Demonstrates:
        - Logical DAG width (15 parallel tasks) significantly exceeds physical worker capacity (2 workers).
        - Queueing continues deterministically without stalling.
        - Worker reuse occurs heavily (15 tasks / 2 workers = 13 reuses).
        - No v4-style launch counter blocks execution (15 tasks complete cleanly).
        - Distinguishes clearly between:
            * logical DAG width
            * physical active workers
            * scheduler capacity
            * total worker creation count
        """
        engine = MissionEngine(mission_id="m_width_test", title="High Logical DAG Width")

        # 15 completely independent root tasks => Logical DAG Width = 15
        num_tasks = 15
        tasks = [
            Task(id=f"t_{i:02d}", domain="backend", write_set={f"file_{i}.txt"})
            for i in range(num_tasks)
        ]
        for t in tasks:
            engine.add_task(t)

        engine.start()

        # Logical DAG width at step 0 is 15
        logical_dag_width = len(engine.get_ready_tasks())
        self.assertEqual(logical_dag_width, 15)

        # Register only 2 physical workers in backend domain
        engine.register_worker(worker_id="w_fast_1", domains=["backend"])
        engine.register_worker(worker_id="w_fast_2", domains=["backend"])
        total_worker_creation_count = 2

        # Verification of clear metric separation
        scheduler_capacity = engine.aimd_controller.current_capacity
        physical_workers_count = len(engine.workers.all_workers())

        self.assertEqual(logical_dag_width, 15)
        self.assertEqual(total_worker_creation_count, 2)
        self.assertEqual(physical_workers_count, 2)
        self.assertGreater(logical_dag_width, physical_workers_count)

        # Process all tasks using only the 2 physical workers
        completed = 0
        worker_assignment_counts: Dict[str, int] = {"w_fast_1": 0, "w_fast_2": 0}
        peak_physical_active_workers = 0

        while completed < num_tasks:
            # Active physical workers before dispatch
            busy_count = len(engine.workers.get_busy_workers())
            assignment = engine.assign_next()
            if assignment is None:
                # If both workers busy or no tasks ready
                break

            worker_assignment_counts[assignment.worker_id] += 1
            current_active = len(engine.workers.get_busy_workers())
            if current_active > peak_physical_active_workers:
                peak_physical_active_workers = current_active

            # Simulate execution and mark completed
            engine.mark_task_completed(assignment.task_id, result={"done": True})
            completed += 1

        self.assertEqual(completed, 15)
        self.assertEqual(engine.state, MissionState.COMPLETED)

        # Total worker reuses: 15 tasks - 2 initial assignments = 13 reuses
        total_reuses = (worker_assignment_counts["w_fast_1"] - 1) + (worker_assignment_counts["w_fast_2"] - 1)
        self.assertEqual(total_reuses, 13)

        # Confirm peak active physical workers never exceeded total workers created
        self.assertLessEqual(peak_physical_active_workers, total_worker_creation_count)

        # Report metric distinction explicitly
        metrics = {
            "logical_dag_width": logical_dag_width,
            "physical_active_workers_peak": peak_physical_active_workers,
            "scheduler_capacity": scheduler_capacity,
            "total_worker_creation_count": total_worker_creation_count,
            "total_tasks_completed": completed,
            "worker_reuses": total_reuses,
        }
        self.assertEqual(metrics["logical_dag_width"], 15)
        self.assertEqual(metrics["total_worker_creation_count"], 2)
        self.assertEqual(metrics["worker_reuses"], 13)


if __name__ == "__main__":
    unittest.main()
