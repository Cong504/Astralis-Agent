"""SQLite 结构化日志：tasks / plans / actions 三表。

架构落点：ARCHITECTURE.md §8.3「日志用 SQLite 存结构化数据」。
设计：按 task_id 可还原完整链路；只追加，不修改、不删除历史记录。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("logs/agent.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT PRIMARY KEY,
    raw_input   TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'cli',
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    plan_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    goal        TEXT NOT NULL,
    plan_json   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
CREATE TABLE IF NOT EXISTS actions (
    action_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    step_id     TEXT,
    action      TEXT NOT NULL,
    tool        TEXT,
    args_json   TEXT,
    result_json TEXT,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);
CREATE INDEX IF NOT EXISTS idx_plans_task   ON plans(task_id);
CREATE INDEX IF NOT EXISTS idx_actions_task ON actions(task_id);
"""


class TaskLogger:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "TaskLogger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def create_task(self, task_id: str, raw_input: str, status: str, created_at: str, source: str = "cli") -> None:
        self.conn.execute(
            "INSERT INTO tasks (task_id, raw_input, source, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (task_id, raw_input, source, status, created_at, created_at),
        )
        self.conn.commit()

    def update_status(self, task_id: str, status: str, updated_at: str) -> None:
        self.conn.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
            (status, updated_at, task_id),
        )
        self.conn.commit()

    def save_plan(self, task_id: str, goal: str, plan: dict[str, Any], created_at: str) -> None:
        self.conn.execute(
            "INSERT INTO plans (task_id, goal, plan_json, created_at) VALUES (?,?,?,?)",
            (task_id, goal, json.dumps(plan, ensure_ascii=False), created_at),
        )
        self.conn.commit()

    def log_action(
        self,
        task_id: str,
        action: str,
        status: str,
        created_at: str,
        step_id: str | None = None,
        tool: str | None = None,
        args: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO actions
               (task_id, step_id, action, tool, args_json, result_json, status, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                task_id,
                step_id,
                action,
                tool,
                json.dumps(args, ensure_ascii=False) if args is not None else None,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                status,
                created_at,
            ),
        )
        self.conn.commit()

    def replay(self, task_id: str) -> dict[str, Any]:
        """按 task_id 还原任务、计划、动作全链路。"""
        task = self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if task is None:
            raise KeyError(f"task_id 不存在: {task_id}")
        plans = [dict(r) for r in self.conn.execute("SELECT * FROM plans WHERE task_id=?", (task_id,))]
        actions = [dict(r) for r in self.conn.execute("SELECT * FROM actions WHERE task_id=?", (task_id,))]
        return {"task": dict(task), "plans": plans, "actions": actions}
