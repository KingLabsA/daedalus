"""Kanban — multi-agent orchestration: task board, goal manager, sub-agents, parallel executor.

Standalone: imports nothing from agent_ultimate.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime


class GoalManager:
    def __init__(self, goal: str):
        self.goal = goal
        self.history = []
        self.completed = False

    def is_complete(self, last_output: str) -> bool:
        if "COMPLETE" in last_output.upper():
            self.completed = True
        return self.completed


# ============== SUB-AGENT ==============
class SubAgent:
    def __init__(self, name: str, task: str, parent_context: str = ""):
        self.name = name
        self.task = task
        self.context = parent_context
        self.result = None
        self.status = "pending"

    def run(self, agent_core) -> str:
        self.status = "running"
        self.result = agent_core.run_loop(
            [
                {"role": "system", "content": agent_core.system_prompt},
                {"role": "user", "content": f"Sub-task '{self.name}': {self.task}\nContext: {self.context}"},
            ],
            max_iters=5,
        )
        self.status = "done"
        return self.result


# ============== PARALLEL EXECUTOR ==============
class ParallelExecutor:
    @staticmethod
    def run(tasks: list[dict], agent_core):
        results = {}
        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = {executor.submit(SubAgent(t["name"], t["prompt"], str(t)).run, agent_core): t["name"] for t in tasks}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        return results


# ============== KANBAN ==============
@dataclass
class KanbanTask:
    id: str
    title: str
    description: str
    status: str = "todo"
    assigned_to: str = None
    retries: int = 0
    max_retries: int = 3
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    agent_context: str = ""


class KanbanWorker:
    def __init__(self, name: str, worker_type: str):
        self.name = name
        self.type = worker_type
        self.status = "idle"
        self.current_task: KanbanTask | None = None
        self.last_heartbeat = datetime.now()

    def heartbeat(self):
        self.last_heartbeat = datetime.now()

    def assign(self, task: KanbanTask):
        self.current_task = task
        self.status = "working"


class KanbanBoard:
    def __init__(self):
        self.tasks: list[KanbanTask] = []
        self.workers: list[KanbanWorker] = []
        self.running = True
        self._guardian = threading.Thread(target=self._guardian_loop, daemon=True)
        self._guardian.start()

    def add_task(self, title, desc="", context="") -> KanbanTask:
        task = KanbanTask(id=f"t-{len(self.tasks) + 1}", title=title, description=desc, agent_context=context)
        self.tasks.append(task)
        return task

    def add_worker(self, name, worker_type):
        self.workers.append(KanbanWorker(name, worker_type))

    def assign_work(self):
        for task in self.tasks:
            if task.status == "todo":
                for w in self.workers:
                    if w.status == "idle":
                        w.assign(task)
                        task.status = "in_progress"
                        task.assigned_to = w.name
                        break

    def _guardian_loop(self):
        while self.running:
            time.sleep(10)
            now = datetime.now()
            for w in self.workers:
                if (now - w.last_heartbeat).total_seconds() > 30 and w.status == "working":
                    print(f"Zombie worker: {w.name}")
                    if w.current_task:
                        t = w.current_task
                        t.status = "todo"
                        t.retries += 1
                        if t.retries > t.max_retries:
                            t.status = "done"
                    w.status = "idle"
                    w.current_task = None

    def move_task(self, task_id: str, to_status: str) -> bool:
        for t in self.tasks:
            if t.id == task_id and to_status in ("todo", "in_progress", "review", "done"):
                t.status = to_status
                return True
        return False

    def remove_task(self, task_id: str) -> bool:
        for i, t in enumerate(self.tasks):
            if t.id == task_id:
                self.tasks.pop(i)
                return True
        return False

    def get_board_state(self):
        return {
            s: [{"id": t.id, "title": t.title, "status": t.status, "assigned_to": t.assigned_to, "retries": t.retries} for t in self.tasks if t.status == s]
            for s in ["todo", "in_progress", "review", "done"]
        }
