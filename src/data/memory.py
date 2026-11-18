"""记忆库：结构化经验 + 标签检索（W3 3.5）。

复用 SQLite（db.py 的 memory 表），不引入向量库（ARCHITECTURE.md §7 硬约束）。
标签从执行轨迹确定性提取，不用模型自由发挥，保证可验证。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def extract_tags(steps: list[dict[str, Any]], denials: list[dict[str, Any]],
                 reflection: dict[str, Any]) -> tuple[str, ...]:
    """从执行轨迹提取结构化标签（确定性规则，非语义/向量）。"""
    tags: set[str] = set()
    for s in steps:
        tool = s.get("tool")
        if tool:
            tags.add(f"tool:{tool}")
        if s.get("verdict") == "DENIED_BY_RULE":
            tags.add("security:denied")
    for d in denials:
        if d.get("matched_rule"):
            tags.add(f"rule:{d['matched_rule']}")
    text = " ".join(str(reflection.get(k, "")) for k in ("improvement", "failure_points"))
    if "tokens" in text or "成本" in text:
        tags.add("cost")
    if "链式" in text:
        tags.add("lesson:chain-gap")
    if not tags:
        tags.add("general")
    return tuple(sorted(tags))


def remember(conn: sqlite3.Connection, task_id: str, tags: tuple[str, ...],
             content: str, created_at: str) -> int:
    """写入一条记忆，返回 memory_id。"""
    cur = conn.execute(
        "INSERT INTO memory (task_id, tags_json, content, created_at) VALUES (?,?,?,?)",
        (task_id, json.dumps(list(tags), ensure_ascii=False), content, created_at),
    )
    conn.commit()
    return int(cur.lastrowid)


def recall(conn: sqlite3.Connection, tag: str) -> list[dict[str, Any]]:
    """按标签检索记忆（结构化 LIKE，非向量相似度）。"""
    rows = conn.execute(
        "SELECT * FROM memory WHERE tags_json LIKE ? ORDER BY memory_id",
        (f'%"{tag}"%',),
    ).fetchall()
    return [dict(r) for r in rows]
