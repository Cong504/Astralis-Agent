# ADR-0001 — append-only 事件日志与五段执行管道

> 版本：v0.1 ｜ 状态：已接受（W1 收口，2026-11-17）｜ 关系：落地 W1-plan.md §1.6/§1.7、ARCHITECTURE.md §3/§8

---

## 背景

W1 需要同时满足两件事：

1. 审计要求：ARCHITECTURE.md §8.3 要求结构化日志可用 task_id 还原完整链路；W1-plan.md §1.7 提出借鉴 DSH 的 append-only 事件日志作为不可变事实源。
2. 演进要求：W1 的 guard/execute 都是桩，W2 要接真执行、W3 要接真实高危拦截。如果接口签名不先定死，W2/W3 会推翻重写。

同时项目已确认方向锚点：自研 v0.1 + 借鉴 DSH 设计 + v0.2 再评估接入。

---

## 决策

1. **双层日志，职责分离**
   - SQLite（tasks / plans / actions）作为查询存储，支持按 task_id 回放。
   - `logs/events.jsonl` 作为 append-only 不可变审计源，每个状态机跃迁一条事件（RECEIVED → … → DONE/FAILED/CANCELLED）。
   - 两者用 task_id 对齐，不互相替代。
2. **五段执行管道接口本周定死**
   - `pre_execute → guard → approve → execute → post_execute`，抽象基类在 `src/pipeline/contract.py`。
   - W1 实现：guard 直接放行（stub）、approve 由 CLI 注入确认结果、execute 只打印执行意图（stub）。
   - W2/W3 填真逻辑时不得改接口签名；若必须改，需新开 ADR。
3. **后端可插拔 + 桩降级**
   - Model 层提供 StubBackend，使无 GPU、无云端 Key 的环境也能端到端演示。
   - 生产配置本地优先、云端兜底；无 Key 时 fallback 退化为 stub 并在日志标注 `backend=stub`，不伪装成真实云端调用。

---

## 备选

| 备选 | 未采用原因 |
|---|---|
| 只写 SQLite，不做事件流 | 无法提供不可变审计源；未来 replay/fork 缺乏事实基础 |
| 只写 JSONL，不做 SQLite | 查询与按 task_id 回放需要全量扫描，不适合结构化审计 |
| W1 先不定义管道接口，W2 再设计 | 会导致 W2/W3 推翻重写，违反「本周定死接口」的约束 |
| 接 DSH 运行时 | 方向锚点是自研 v0.1，DSH 只借设计，v0.2 再评估 |

---

## 后果

**正面**

- W1 在真实 GPU/Key 缺失时仍可端到端演示与验证（StubBackend）。
- 审计轨迹同时具备「可查询」与「不可变」两种性质。
- W2/W3 可在不改接口的前提下替换 guard 与 execute 实现。

**代价 / 约束**

- 双写日志带来一致性责任：task_id 必须由 Interface 层统一生成并贯穿两层。
- 事件流为 append-only，禁止修改或删除历史日志（AGENTS.md 红线）。
- 任何对管道段顺序或签名的改动必须走新 ADR，不得就地修改。

---

## 验收证据（2026-11-17）

- `python -m pytest tests/ -q`：11 passed（parser 5 / logging 4 / smoke 2）。
- CLI 实跑：`agent run "<task>" --no-confirm` 输出步骤计划与「本应执行…(未真跑)」，`agent log <task_id>` 回放 DONE 状态及 5 条状态跃迁事件。
- `git check-ignore logs/agent.db logs/events.jsonl`：两者均被忽略，运行时日志不入库。
