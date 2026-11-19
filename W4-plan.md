# W4 plan.md — 第 4 周：Agent 打包与整体验收（任务分解，待确认）

> 版本：v0.1 ｜ 周期：第 4 周（D1–D5）｜ 状态：**待人类确认**（AGENTS.md §6 门禁：经确认后才开工；W3 已于 2026-11-19 正式收口）
> 上游依据：DEVELOPMENT_PLAN.md §W4（4.1–4.7）、PACKAGE_SPEC.md、PRD.md §7 成功标准、W3-plan.md 带入项、W3-retro.md §6.1
> 去重说明：权限声明（tools/paths/network）在 `PACKAGE_SPEC.md §6` 与 W2 tools spec 已定义；signature 字段在包规范已定稿。W4 把它们落进导出包，不重做设计。

---

## 0. Carry-over 清单（W4 强制前置项，不得沉入 backlog）

> 用户在 W4 启动前明确要求：把带入项建成显式清单，挂为 W4 强制前置项。
> **分两类处置**（人类裁决 2026-11-19）：CO-1/CO-2 是**环境依赖待触发**；CO-3 改为 **W4 内主动闭合项**（不等环境、主动 push 真跑实证）。
> 所有项均不阻塞"开始 W4 编码"，但必须在 W4 整体验收报告（4.6）出结论前逐条处置；CO-3 禁止与 CO-1/CO-2 混列为"待环境"。

| 编号 | 带入项 | 来源 | 触发条件 | 验收标准 | 当前状态 | 处置类别 |
|---|---|---|---|---|---|---|
| CO-1 | **W2-C1 补 cold-after-pull 冷启动样本** | W2-plan §2.8 / A4 | 新环境（镜像首次拉取）到位时 | `python scripts/bench_cold_start.py --samples 20 --cold-after-pull`，标注首次拉镜像，原始样本落盘，与 warm p95（0.7002s）对照，确认 cold 是否仍 ≤ N=1.0s | 待环境（本机镜像已缓存；协议已支持 `--cold-after-pull`） | 环境依赖 |
| CO-2 | **W2-R10 网络白名单真实出网验证** | W2-plan 真实镜像验收清单 | 具备白名单网络环境时 | 白名单域名可出站 443、非白名单被拒；当前纯 mock 关闭 | 待环境（逻辑 mock 已关闭，真实出网未验） | 环境依赖 |
| CO-3 | **A1' CI artifact 明细缺口 → 实证闭环** | W3-retro.md §6.1 | **W4 内主动执行**（非环境依赖） | ① `a1-gate.yml` 修复已 commit（`dd7e274`，同时上传 `a1_gate.jsonl` + `bench_a1_legality.json`）；② 4.6 报告前由 Agent 主动 push 一个 trigger-path 改动触发真 CI；③ 下载新 artifact，确认 `bench_a1_legality.json` 含 10 条逐条明细（raw/prompt_tokens/completion_tokens）；④ 4.6 报告写 **「已实证闭环」** | 配置已提交，待 W4 内主动真跑验证 | **W4 主动闭合** |

**CO-3 主动闭合执行协议（W4 内，不等环境）**：
1. 修复已 commit（`dd7e274`）——`.github/workflows/a1-gate.yml` 的 artifact path 已含两个文件；
2. 在 4.6 报告出结论前，Agent 做最小非语义 trigger-path 改动（如 `src/model/parser.py` 加一行注释），push 到 `main`（Key 已注入，用户已授权同类操作先例）；
3. CI 真跑后下载新 artifact，逐条核对 `bench_a1_legality.json` 的 10 条明细字段完整；
4. 证据写入 4.6 报告，CO-3 标「已实证闭环」，不与 CO-1/CO-2 混列。

> CO-3 背景：3.8 关闭用的是 02:41 PASS 汇总行（判据齐全、关闭有效）；但那次 CI 的逐条明细未随 artifact 上传，仓库内 `logs/bench_a1_legality.json` 是 W2 本地真跑的同类记录、非 CI 那次。故本轮主动补一次真跑，让 CI 明细进包、证据链完整。

---

## 1. 本周唯一目标

按 `PACKAGE_SPEC.md` 完成 Agent 包导出/恢复（7 部分齐全、哈希正确、恢复后同任务跑通），完成 PRD 五条成功标准逐项实测，产出 v0.1 验收报告与 v0.2 backlog 排序。

---

## 2. 任务分解（D1–D5，按依赖排序）

> 负责列：A = 开发 Agent 实现，H = 人类决策/验收。

### 4.1 实现包导出（按 PACKAGE_SPEC.md 目录结构）｜ D1–D2 ｜ A
- 做什么：写 `src/package/exporter.py`，收集 prompt/skills/tools/config/memory，计算 `manifest.hash.value`（排除 signature.json）与 `signature.permissions_digest`，结构校验后压缩为 `<name>-v<version>.agentpkg` 放入 `packages/`。
- 验收：
  - 7 个部分齐全，manifest 哈希正确（排除 signature.json）；
  - **signature.json 空壳存在且 `permissions_digest` 真实写入**（与 tools.json.permissions 重算一致；签名算法字段仍为 null，不实现签名逻辑）；
  - 导出失败报具体文件/字段，不静默产坏包。
- 对应：PACKAGE_SPEC.md §2/§3/§9/§10。

### 4.2 实现包恢复与加载 ｜ D2–D3 ｜ A｜H 验收
- 做什么：写 `src/package/importer.py`，按 PACKAGE_SPEC.md §11 流程：校验扩展名/zip → spec_version 与 min_runtime → 重算 hash 比对 → 重算 permissions_digest → 权限取交集 → 加载注册 → 记恢复事件。四种拒绝情况打印明确原因。
- 验收（S2 同任务对比，固化流程防"看起来跑通"）：
  - **输入固化**：S2 场景固定输入 = 一个含已知文件的目录（如 `notes.txt`/`data.csv`/`sub/deep.md`），任务描述「扫描目录并生成 Markdown 报告」，与 `scripts/run_s2_smoke.py` 同构。
  - **期望固化**：报告含全部输入文件名；状态 DONE；导出前的执行轨迹（plan/动作/usage/denials/复盘）落盘 `logs/s2_before_export.json` 作为基线。
  - **恢复后逐字段比对**：从导出的 `.agentpkg` 恢复 Agent，跑同一 S2 输入，将结果与基线逐字段比对——goal、steps 数与 tool 序列、报告内容（文件名集合）、status、denials 数、复盘三字段须一致；任一字段不一致即判失败并打印差异字段，不允许"跑通就算过"。
- 对应：PACKAGE_SPEC.md §11/§12。

### 4.3 signature.json 空壳落地 ｜ D2 ｜ A
- 做什么：按 PACKAGE_SPEC.md §9 写空壳字段（algorithm/public_key/signature/signed_at/signer 为 null，`trust=local-unverified`，`permissions_digest` 真实计算，notes 注明不实现签名逻辑）。
- 验收：结构完整，校验器读取不报错，不实现签名逻辑。

### 4.4 权限声明字段写入 ｜ D2 ｜ A
- 做什么：导出包内 tools.json 的 permissions（filesystem read/write、network enabled/allowlist、resources、forbidden_commands_inherited=true）从当前 Tools spec 与 SANDBOX_RULES 生成。
- 验收：导出包能读出「这个 Agent 声明要用什么」；`forbidden_commands_inherited=false` 的包导入时拒绝。

### 4.5 连续 10 次稳定性测试 ｜ D3–D4 ｜ A｜H 验收
- 做什么：用 S2 场景连续跑 10 次，记录崩溃/异常；**每次标注执行路径是 stub 还是真沙箱**。
- **证据分级口径（人类裁决 2026-11-19）**：
  - **只有真沙箱（或与生产等价路径）10/10 通过**，PRD 成功标准 1 才算「**达成**」；
  - 若本轮只跑 stub（如 Key/环境不具备），4.6 报告必须标「**部分达成（stub）**」，并把「**真沙箱 10/10**」列为已知限制带入项；
  - **禁止静默标绿**——不得用 stub 10/10 冒充成功标准 1 达成，也不得在 4.6 只写"10/10 通过"而不注明执行路径。
- 验收：真沙箱 10/10 不崩溃则达成；否则按上述口径标部分达成 + 已知限制带入项。

### 4.6 PRD 成功标准逐项实测并出报告 ｜ D4 ｜ A｜H 验收
- 做什么：PRD §7 共 7 条成功标准（含云端 A1' 与 Token 护栏两条 W3 新增）逐条给实测证据，未达标项写明差距；**Carry-over 逐条处置**：CO-1/CO-2 环境依赖标"待环境"并说明不构成 v0.1 阻断的理由；CO-3 必须标"**已实证闭环**"（附新 artifact 的 10 条明细证据），不得混列待环境。
- 产出：`W4-acceptance.md`（验收报告）。
- 验收：每条有实测证据或明确差距说明。

### 4.7 第一版复盘文档 + v0.2 backlog 排序 ｜ D5 ｜ A｜H 验收
- 做什么：汇总 W1–W4 复盘，明确 v0.2 backlog（网页提取、PDF/Excel、定时任务、专用 Skill 容器等的实施顺序）。
- 验收：网页提取进 v0.2 顺序明确。

### 周收口 ｜ D5 ｜ A｜H 验收
- 对照下方验收清单逐条打分；出 `W4-retro.md`；v0.1 交付物清单归档。

---

## 3. 周五验收清单（H 逐条打分）

- [ ] CO-1 / CO-2（环境依赖）：逐条处置，未到位的写明"待环境"差距，不静默忽略
- [ ] **CO-3（W4 主动闭合）**：真跑新 artifact 含 `bench_a1_legality.json` 10 条逐条明细，4.6 报告写「已实证闭环」，不与 CO-1/CO-2 混列
- [ ] 4.1 导出包 7 部分齐全、manifest 哈希正确、signature 空壳存在且 permissions_digest 真实写入
- [ ] 4.2 S2 输入/期望/导出前结果落盘，恢复后逐字段比对一致、四种拒绝情况明确
- [ ] 4.3 signature 空壳字段完整、不实现签名逻辑
- [ ] 4.4 权限声明可读取、forbidden_commands_inherited 校验
- [ ] 4.5 连续 10 次：真沙箱 10/10 才算「达成」；仅 stub 则标「部分达成（stub）」+ 真沙箱 10/10 带入项，禁止静默标绿
- [ ] 4.6 PRD 七条成功标准逐项实测 + 验收报告（CO 三类处置结论入报告）
- [ ] 4.7 v0.2 backlog 排序明确

---

## 4. 本周明确不做

- 不实现签名实际生效逻辑（v0.1 空壳，PACKAGE_SPEC §9）
- 不做公开市场/在线交换（PRD 非目标）
- 不引入向量库、不做本地模型部署
- 不把 W1–W3 已交付的基础重做

---

## 5. 风险与砍范围预案

| 触发 | 处置 |
|---|---|
| 导出/恢复哈希不一致 | 拒绝加载并打印具体原因，不静默猜测 |
| Carry-over 环境始终不到位 | 4.6 标"待环境"，不伪造结论；由 H 决定是否作为 v0.1 已知限制 |
| 10 次稳定性出现崩溃 | 定位并修复，不达 10/10 不关闭 4.5 |
| 新想法 | 一律进 PRD backlog，不进本周任务表 |

---

## 6. 开工门禁

本文件经人类确认后开工。Carry-over 清单（§0）为强制前置项，在 4.6 出报告前逐条处置。**未经确认不启动任何 W4 编码。**
