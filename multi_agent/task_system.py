"""s12 Task System：Router plan → 落盘任务图（learn-claude-code 风格）。

定位（对照 learn-claude-code）：
  - s05 TodoWrite = 会话内 checklist → 本项目用 Router 的 plan 列表
  - s12 Task System = 磁盘任务 + blockedBy 依赖 → 本模块

用法：
  tm = TaskManager()
  tm.materialize_from_plan(plan, query)   # Router 产出 plan 后
  tm.on_agent_finished("sql")             # 某 Agent 节点跑完后
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


TASKS_DIR = Path(__file__).resolve().parent.parent / ".tasks"
# Not .json: Opik endpoint watches *.json and would restart on task writes.
TASK_FILE_SUFFIX = ".task"


@dataclass
class Task:
    id: str
    subject: str
    description: str = ""
    status: str = "pending"  # pending | in_progress | completed
    owner: str | None = None
    agent: str | None = None  # 对应 plan[].agent
    blockedBy: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        return cls(
            id=data["id"],
            subject=data.get("subject", ""),
            description=data.get("description", ""),
            status=data.get("status", "pending"),
            owner=data.get("owner"),
            agent=data.get("agent"),
            blockedBy=list(data.get("blockedBy") or []),
        )


class TaskManager:
    """文件持久化任务图：create / get / update / list / claim / complete。"""

    def __init__(self, tasks_dir: Path | str | None = None):
        self.tasks_dir = Path(tasks_dir) if tasks_dir else TASKS_DIR
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._run_ids: list[str] = []  # 本轮 materialize 产生的 task id

    def _path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}{TASK_FILE_SUFFIX}"

    def save(self, task: Task) -> None:
        self._path(task.id).write_text(
            json.dumps(task.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def create(
        self,
        subject: str,
        description: str = "",
        blockedBy: list[str] | None = None,
        owner: str | None = None,
        agent: str | None = None,
        task_id: str | None = None,
    ) -> Task:
        tid = task_id or f"task_{int(time.time())}_{uuid.uuid4().hex[:4]}"
        task = Task(
            id=tid,
            subject=subject,
            description=description,
            status="pending",
            owner=owner,
            agent=agent,
            blockedBy=list(blockedBy or []),
        )
        self.save(task)
        return task

    def get(self, task_id: str) -> Task | None:
        path = self._path(task_id)
        if not path.exists():
            # Backward compat: legacy task_*.json from before TASK_FILE_SUFFIX
            legacy = self.tasks_dir / f"{task_id}.json"
            if not legacy.exists():
                return None
            path = legacy
        return Task.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def update(self, task_id: str, **fields) -> Task:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        for key, value in fields.items():
            if hasattr(task, key):
                setattr(task, key, value)
        self.save(task)
        return task

    def list_all(self) -> list[Task]:
        tasks = []
        seen: set[str] = set()
        # Prefer .task; also read legacy .json for one-release backward compat
        paths = list(self.tasks_dir.glob(f"task_*{TASK_FILE_SUFFIX}"))
        paths += [p for p in self.tasks_dir.glob("task_*.json") if p.stem not in {x.stem for x in paths}]
        for path in sorted(paths, key=lambda p: p.name):
            try:
                task = Task.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if task.id in seen:
                continue
            seen.add(task.id)
            tasks.append(task)
        return tasks

    def can_start(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        for dep_id in task.blockedBy:
            dep = self.get(dep_id)
            if dep is None or dep.status != "completed":
                return False
        return True

    def claim(self, task_id: str, owner: str = "agent") -> str:
        task = self.get(task_id)
        if task is None:
            return f"Task {task_id} not found"
        if task.status != "pending":
            return f"Task {task_id} is {task.status}, cannot claim"
        if not self.can_start(task_id):
            blocked = [
                d for d in task.blockedBy
                if (self.get(d) is None or self.get(d).status != "completed")
            ]
            return f"Blocked by: {blocked}"
        task.owner = owner
        task.status = "in_progress"
        self.save(task)
        return f"Claimed {task_id} ({task.subject})"

    def complete(self, task_id: str) -> str:
        task = self.get(task_id)
        if task is None:
            return f"Task {task_id} not found"
        task.status = "completed"
        self.save(task)
        unblocked = [
            t.subject for t in self.list_all()
            if t.status == "pending" and t.blockedBy and self.can_start(t.id)
        ]
        msg = f"Completed {task_id} ({task.subject})"
        if unblocked:
            msg += f"\nUnblocked: {', '.join(unblocked)}"
        return msg

    # ── 与 Router plan 的桥接 ──────────────────────────────────────────

    def materialize_from_plan(self, plan: Iterable[dict], query: str = "") -> list[Task]:
        """把 Router plan 落成串行依赖任务图，并 claim 第一个。

        plan: [{"agent": "sql", "task": "..."}, {"agent": "hive", "task": "..."}]
        → task_sql blockedBy=[] → task_hive blockedBy=[task_sql]
        """
        steps = [s for s in plan if s.get("agent")]
        if not steps:
            self._run_ids = []
            return []

        # 新一轮查询：只追踪本轮 id，旧文件保留便于调试
        created: list[Task] = []
        prev_id: str | None = None
        run_tag = uuid.uuid4().hex[:6]
        for i, step in enumerate(steps):
            agent = step["agent"]
            subject = step.get("task") or agent
            tid = f"task_{run_tag}_{i:02d}_{agent}"
            task = self.create(
                subject=subject[:120],
                description=f"query={query[:200]}" if query else "",
                blockedBy=[prev_id] if prev_id else [],
                agent=agent,
                task_id=tid,
            )
            created.append(task)
            prev_id = tid

        self._run_ids = [t.id for t in created]
        # claim 第一个可执行任务
        if created and self.can_start(created[0].id):
            self.claim(created[0].id, owner=created[0].agent or "agent")
        self.print_board(title="Task board (from Router plan)")
        # 返回磁盘上的最新状态（claim 后 status 已变）
        return [self.get(t.id) for t in created]

    def on_agent_finished(self, agent: str) -> None:
        """Agent 节点完成后：complete 对应 in_progress 任务，并 claim 下一个。"""
        if not self._run_ids:
            return
        current = None
        for tid in self._run_ids:
            t = self.get(tid)
            if t and t.agent == agent and t.status == "in_progress":
                current = t
                break
        if current is None:
            # 容错：同 agent 的 pending 且可启动也算
            for tid in self._run_ids:
                t = self.get(tid)
                if t and t.agent == agent and t.status == "pending" and self.can_start(tid):
                    self.claim(tid, owner=agent)
                    current = self.get(tid)
                    break
        if current is None:
            return

        print(f"   ✅ Task complete: {self.complete(current.id)}")

        # claim 下一个已解锁的本轮任务
        for tid in self._run_ids:
            t = self.get(tid)
            if t and t.status == "pending" and self.can_start(tid):
                print(f"   ▶ Task claim: {self.claim(tid, owner=t.agent or 'agent')}")
                break
        self.print_board(title="Task board")

    def print_board(self, title: str = "Tasks") -> None:
        ids = self._run_ids or [t.id for t in self.list_all()[-8:]]
        if not ids:
            return
        icon = {"pending": " ", "in_progress": "▸", "completed": "✓"}
        lines = [f"📋 todo {title}"]
        for tid in ids:
            t = self.get(tid)
            if not t:
                continue
            mark = icon.get(t.status, "?")
            owner = f" @{t.owner}" if t.owner else ""
            lines.append(f"  [{mark}] {t.agent or '-':12} {t.subject[:60]}{owner}")
        print("\n".join(lines))
