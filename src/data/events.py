"""Append-only 事件日志（JSONL）。

设计依据：W1-plan.md §1.7「借鉴 DSH 的 append-only 事件日志，作为不可变审计源，
为未来 replay/fork 打底」。每个状态机跃迁一条事件，只追加、不修改、不删除。
事件里的 task_id 与 SQLite tasks 表对齐。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_EVENTS_PATH = Path("logs/events.jsonl")

# ARCHITECTURE.md §3 状态机的全部状态
VALID_STATES = {
    "RECEIVED", "PLANNING", "AWAIT_CONFIRM", "EXECUTING",
    "REFLECTING", "DONE", "FAILED", "CANCELLED",
}

_WRITE_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EventLog:
    def __init__(self, path: Path | str = DEFAULT_EVENTS_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        task_id: str,
        state: str,
        detail: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        if state not in VALID_STATES:
            raise ValueError(f"非法状态: {state}")
        event = {
            "seq": self._next_seq(),
            "task_id": task_id,
            "state": state,
            "timestamp": timestamp or utc_now(),
            "detail": detail or {},
        }
        with _WRITE_LOCK, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def _next_seq(self) -> int:
        if not self.path.exists():
            return 1
        with self.path.open("r", encoding="utf-8") as fh:
            return sum(1 for _ in fh) + 1

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def for_task(self, task_id: str) -> list[dict[str, Any]]:
        return [e for e in self.read_all() if e["task_id"] == task_id]
