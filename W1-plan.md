# W1 plan.md — 第 1 周：打通最小闭环骨架

> 版本：v0.1 ｜ 周期：第 1 周（D1–D5）｜ 状态：已确认并执行（W1 收官于 2026-11-18）｜ 路线更新见 ADR-0003
> 上游依据：DEVELOPMENT_PLAN.md §W1（1.1–1.8）、ARCHITECTURE.md §2/§3/§7/§9、PACKAGE_SPEC.md
> 本周方向锚点（第 9/10 轮已确认）：自研 v0.1 + 借鉴 DSH 设计 + v0.2 再评估接入
> **路线变更（2026-11-18，ADR-0003）**：云端先行——1.3（本地 14B 部署）与 A1（本地合法率实测）标记 Cancelled/N-A；GPU/nvidia-smi 相关项在本机 N-A；W1 实际用云端路由 + stub 打通闭环。

---

## 0. 本周唯一目标

跑通 输入任务 → 生成结构化计划 → 用户确认 → 打印「本应执行的命令」（不真跑）→ 全链路落日志。

- 本周不接真执行（真执行是 W2）。判断标准：W1 结束时能演示一条任务从输入到日志回放，且沙箱、工具都还是「桩」。
- 本周要拿到三个硬结论（见 §3），其中「本地 14B 结构化输出合法率」不达标会改写后续所有「本地为主」的设计，必须最先测、不能拖到周三才发现。

分工原则（对齐沟通流程）：你负责收敛与验收签字，开发 Agent 负责实现；本文件即开发 Agent 本周的作业单。

---

## 1. 任务分解（1.1–1.8 展开为可执行作业）

> 预估按 5 个工作日排；「负责」列：A=开发 Agent 实现，H=人类决策/验收。

### 1.1 初始化仓库与目录骨架 ｜ D1 ｜ A｜H 验收
- 做什么：按 AGENTS.md §6 建 local-agent/ 结构（6 份规范文档已存在，纳入 git；建 docs/decisions/、docs/api/、src/、tests/、packages/）。
- 产出：首个 commit（chore: bootstrap repo + spec docs）；.gitignore（屏蔽 venv、模型权重、Docker 构建缓存、*.db）。
- 验收：git log 有提交；目录结构与 §6 一致。
- 本周新增约束：仓库内只放 Markdown/代码，不放任何 Word/二进制文档（承接第 7 轮结论，Word 草稿留个人目录作历史参考）。

### 1.2 选框架并跑通 Hello World（OpenClaw）｜ D1 ｜ A
- 做什么：装 OpenClaw，加载一个最小 Agent，完成一次模型调用（先连云端 API 打通链路，本地模型在 1.3 接入）。
- 产出：src/hello_agent.py，能打印一次模型返回。
- 验收：一条命令跑通「输入→模型→输出」，不依赖沙箱。
- 风险点：若 OpenClaw 与 MCP Stdio 集成不顺，当周不切换框架，先记录问题，用最小自研 loop 顶替（自研底座方针允许）。

### 1.3 部署本地模型（Qwen2.5-14B 量化 + vLLM）｜ **Cancelled（ADR-0003）** ｜ N/A
- 状态：本地 LLM 暂缓，`deploy_14b.sh` 标记「待本地硬件就绪后启用」，不进 W1 验收。
- 原做什么（存档）：3090 上部署 14B 量化版（vLLM 或 llama.cpp server 二选一，优先 vLLM，因吞吐与 OpenAI 兼容接口）；暴露 OpenAI 兼容 endpoint 给运行时。
- 产出：config/model.local.json（endpoint、模型名、温度、max_tokens）。
- 验收：单次推理延迟 < 5s，能稳定输出可解析 JSON。
- 注意：这一项是 §3 假设 A1 的前置，优先于 1.4 完成。

### 1.4 定义计划 JSON Schema ｜ D2 ｜ A
- 做什么：写步骤列表 Schema，字段至少含 step_id / action / tool / args / risk_level，与 PACKAGE_SPEC.md 的 risk_level 枚举对齐（low/medium/high）。
- 产出：src/schema/plan.schema.json。
- 验收：Schema 可被 jsonschema 加载；给一个样例计划能通过校验。

### 1.5 模型结构化输出 + 解析校验层 ｜ D2–D3 ｜ A
- 做什么：Model 层强制 JSON 输出（用 JSON mode / grammar 约束），Runtime 侧加校验层：非法 JSON 捕获 → 触发 1 次重试 → 仍失败进降级（见 1.8）。
- 产出：src/model/client.py、src/model/parser.py + 单元测试。
- 验收：故意喂一个产生非法 JSON 的 prompt，能观测到「捕获→重试→降级」完整路径。
- 架构落点：对应 ARCHITECTURE.md §8.1「模型层必须输出结构化 JSON」。

### 1.6 CLI 交互层 + 执行意图打印 ｜ D3 ｜ A｜H 验收
- 做什么：输入任务 → 展示步骤计划 → 确认/取消。确认前不触发任何副作用；确认后 W1 只打印「本应执行的命令/工具调用」，不真跑。取消则日志写 CANCELLED。
- 产出：src/cli/main.py（agent run "<task>" 子命令）。
- 验收：取消路径日志含 CANCELLED；确认路径日志含 AWAIT_CONFIRM→EXECUTING(stub)。
- 借鉴 DSH 的落点：在 CLI 与执行之间插入五段执行管道接口——
  pre_execute → guard（守卫/高危拒绝）→ approve（审批/确认）→ execute → post_execute。
  W1 阶段 guard 和 approve 用桩实现（guard 直接放行、approve 走 CLI 确认），但接口签名本周就要定死，W2/W3 再填真逻辑，避免推翻重写。
  产出物：src/pipeline/contract.py（管道五段的抽象基类与数据契约）。

### 1.7 日志：SQLite 结构化三表 + append-only 事件流 ｜ D3–D4 ｜ A
- 做什么：两块并行落地。
  1. SQLite 三表：tasks / plans / actions，按 task_id 可还原完整链路（对应 ARCHITECTURE.md §8.3）。
  2. 借鉴 DSH 的 append-only 事件日志：额外写一份只追加的 JSONL 事件流（每个状态机跃迁一条事件：RECEIVED/PLANNING/AWAIT_CONFIRM/EXECUTING/REFLECTING/DONE/FAILED/CANCELLED），作为不可变审计源，为未来 replay/fork 打底。
- 产出：src/data/db.py、src/data/events.py、logs/events.jsonl。
- 验收：跑一条任务后，SQLite 能按 task_id 查全链路，且 events.jsonl 有对应跃迁事件；两者可通过 task_id 对齐。
- 取舍说明：SQLite 是查询存储，JSONL 是不可变事实源，二者不冲突；这是本周把「自研 + 抄 DSH 事件日志」这条决策落地的地方（需出 ADR-0001）。

### 1.8 云端兜底路由 ｜ D4 ｜ A
- 做什么：Model 层做本地/云端双后端路由；本地失败（超时 / 非法 JSON 重试耗尽 / API 错误）自动切云端，不中断主流程。
- 产出：src/model/router.py；config/model.cloud.json。
- 验收：人为把本地 endpoint 打挂（改错端口），任务能自动走云端完成，日志标注 fallback=cloud。

### 本周收口 ｜ D5 ｜ A｜H 验收
- 1.9 集成冒烟：串起 1.6→1.5→1.7→1.8，跑通 §0 的演示脚本。
- 1.10 出 ADR-0001（append-only 事件日志 + 五段执行管道，自研借鉴 DSH）。
- 1.11 周五对照 §4 验收清单逐条打分，未过项写明差距与处置（不进 backlog 就砍范围）。

---

## 2. 本周末可演示产物

> 对齐 DEVELOPMENT_PLAN.md 的 W1 里程碑。

演示脚本：输入「统计指定目录下的文件数量与类型分布」
→ 展示结构化步骤计划（含 step_id/risk_level）
→ 用户确认
→ 打印「本应执行的工具调用」（dir_scan，W1 不真跑）
→ agent log <task_id> 回放：SQLite 三表链路 + events.jsonl 状态跃迁。

---

## 3. 本周三项硬结论实测（对应 ARCHITECTURE §9，必须出书面结论）

| 编号 | 假设 | 测试方式 | 成功信号 | 失败动作（当周决策） |
|---|---|---|---|---|
| A1 | ~~本地 Qwen2.5-14B 能稳定做结构化规划~~ → **N/A（ADR-0003，硬件暂缓）** | — | — | 本地路线待 RTX 5060Ti 16G / V100 32G 就绪后重启 |
| A1'（云端） | 云端 API（DeepSeek）能稳定做结构化规划 | 10 个日常任务输入，统计合法 JSON 率 | 10 次 ≥8 次合法且 p95<30s | 手动切换 Qwen 或收紧 grammar → 出 ADR（已出 ADR-0003） |
| A2 | 五段执行管道接口能覆盖 W2 落地 | 用桩实现跑通 1.6 确认/取消/意图打印 | 桩逻辑无需改接口即可接真执行 | 接口本周内调整，不留到 W2 |
| A3 | MCP Stdio 能承载 5 个基础工具的接入形态 | 先用 Stdio echo 打通「运行时↔工具进程」一次调用 | 能完成一次进程间工具调用握手 | 工具层回退自定义插件接口 → 出 ADR |

> A1 建议 D1–D2 完成即测（在 1.3 部署好后立刻做），不要等全周功能堆完。A3 是轻量握手验证，不要求 5 个工具都写完（那是 W2 的 2.6）。

---

## 4. 周五验收清单（H 逐条打分：通过 / 未通过 / 部分通过）

- [ ] 1.1 仓库骨架与文档入库，git 首个 commit，无二进制文档
- [ ] 1.2 OpenClaw（或自研 loop）跑通一次模型调用
- [ ] 1.3 14B 本地服务可用，延迟<5s，能出 JSON
- [ ] 1.4 plan.schema.json 存在且样例通过校验
- [ ] 1.5 非法 JSON 能捕获并触发 1 次重试
- [ ] 1.6 确认前无副作用；取消留 CANCELLED；五段管道接口签名已定
- [ ] 1.7 SQLite 三表可回放 + events.jsonl append-only 事件可对齐
- [ ] 1.8 人为本地失败可自动降级云端且日志标注
- [ ] A1/A2/A3 三项均有书面结论（过/不过 + 后续动作）
- [ ] ADR-0001 已提交

---

## 5. 环境与准备清单（D1 前由你/Agent 备齐）

- 硬件：3090 主机（CUDA 驱动就绪）、NAS 作为权重与备份存储挂载点。
- 权重：Qwen2.5-14B-Instruct 量化版（Q4/Q5，先下一份到 NAS，避免 D1 卡在下载）。
- 软件栈：Python 3.11+ / 虚拟环境 / vLLM（或 llama.cpp server）/ OpenClaw / docker CLI（W2 才用，先装好）/ sqlite3。
- 云端兜底：至少一个已备案国产模型 API Key（用于 1.8，Key 只留在运行时侧，绝不进容器，见第 6 轮硬约束）。
- 代码仓：远端 git（私有），用于历史参考与协作；Word 草稿不入仓。

---

## 6. 风险与砍范围预案（超期只砍范围不延周期）

| 触发 | 处置 |
|---|---|
| 14B 部署/延迟不达标 | 立即降 7B 测 A1；仍不达标则本周即转「云端为主」，不拖到 W2 |
| OpenClaw 集成阻塞 >1 天 | 用最小自研 loop 顶替，框架问题记录进 backlog，本周不换框架 |
| 五段管道接口反复返工 | 停功能开发，先把接口对齐 §3 的 A2 定死再往下 |
| D1 卡在模型下载 | 权重必须提前放 NAS（§5）；临时用云端跑通 1.2/1.5 骨架 |
| 任何新想法（如加记忆层/Warm 层） | 一律进 PRD backlog，不进本周任务表（承接第 8/9 轮克制原则） |

---

## 7. 本周明确不做（防止底座做太厚）

- 不做真沙箱执行、不做真工具（W2）。
- 不做高危命令真实拦截（W3，本周 guard 为桩）。
- 不做复盘生成、不做向量检索、不做 Warm/L1 记忆层（第 8/9 轮已砍，L0 上下文 + L2 会话日志本周仅落表，语义检索延后）。
- 不做 Agent 包导出/恢复（W4）。
- 不接入 DSH 运行时，只借它的事件日志与管道设计（第 10 轮结论）。

---

> 确认本文件后，开发 Agent 从 1.1 + 1.3 并行开工（骨架与模型部署同步推进，A1 优先出结论）。
