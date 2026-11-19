# W4 plan.md — 第 4 周：Agent 打包与整体验收（任务分解，待确认）

> 版本：v0.1 ｜ 周期：第 4 周（D1–D5）｜ 状态：**待人类确认**（AGENTS.md §6 门禁：经确认后才开工；W3 已于 2026-11-19 正式收口）
> 上游依据：DEVELOPMENT_PLAN.md §W4（4.1–4.7）、PACKAGE_SPEC.md、PRD.md §7 成功标准、W3-plan.md 带入项、W3-retro.md §6.1
> 去重说明：权限声明（tools/paths/network）在 `PACKAGE_SPEC.md §6` 与 W2 tools spec 已定义；signature 字段在包规范已定稿。W4 把它们落进导出包，不重做设计。

---

## 0. Carry-over 清单（W4 强制前置项，不得沉入 backlog）

> 用户在 W4 启动前明确要求：把这两个 W2 带入项建成显式清单，挂为 W4 强制前置项。
> 三项均不阻塞"开始 W4 编码"，但**必须在 W4 整体验收报告（4.6）出结论前逐条处置**，未处置项写明差距与责任。

| 编号 | 带入项 | 来源 | 触发条件 | 验收标准 | 当前状态 |
|---|---|---|---|---|---|
| CO-1 | **W2-C1 补 cold-after-pull 冷启动样本** | W2-plan §2.8 / A4 | 新环境（镜像首次拉取）到位时 | `python scripts/bench_cold_start.py --samples 20 --cold-after-pull`，标注首次拉镜像，原始样本落盘，与 warm p95（0.7002s）对照，确认 cold 是否仍 ≤ N=1.0s | 待环境（本机镜像已缓存，无法复现首次拉镜像；协议已支持 `--cold-after-pull`） |
| CO-2 | **W2-R10 网络白名单真实出网验证** | W2-plan 真实镜像验收清单 | 具备白名单网络环境时 | 白名单域名可出站 443、非白名单被拒；当前纯 mock 关闭 | 待环境（逻辑 mock 已关闭，真实出网未验） |
| CO-3 | **A1' CI artifact 明细缺口修复** | W3-retro.md §6.1（W4 前置核对发现） | 已修复配置，待下次门禁真跑验证 | `.github/workflows/a1-gate.yml` 已同时上传 `a1_gate.jsonl` + `bench_a1_legality.json`；下次真跑后确认 CI artifact 含 10 条逐条明细（raw/prompt_tokens/completion_tokens） | 配置已修复（commit 待提交），待下次真跑确认 |

> CO-3 说明：3.8 关闭用的是 02:41 PASS 汇总行（4 项判据齐全，关闭有效）；但那次 CI 的逐条明细未随 artifact 上传，仓库内 `logs/bench_a1_legality.json` 是 W2 本地真跑的同类记录、非 CI 那次。已修 artifact 配置，后续真跑证据链完整。

---

## 1. 本周唯一目标

按 `PACKAGE_SPEC.md` 完成 Agent 包导出/恢复（7 部分齐全、哈希正确、恢复后同任务跑通），完成 PRD 五条成功标准逐项实测，产出 v0.1 验收报告与 v0.2 backlog 排序。

---

## 2. 任务分解（D1–D5，按依赖排序）

> 负责列：A = 开发 Agent 实现，H = 人类决策/验收。

### 4.1 实现包导出（按 PACKAGE_SPEC.md 目录结构）｜ D1–D2 ｜ A
- 做什么：写 `src/package/exporter.py`，收集 prompt/skills/tools/config/memory，计算 `manifest.hash.value`（排除 signature.json）与 `signature.permissions_digest`，结构校验后压缩为 `<name>-v<version>.agentpkg` 放入 `packages/`。
- 验收：7 个部分齐全，manifest 哈希正确；导出失败报具体文件/字段，不静默产坏包。
- 对应：PACKAGE_SPEC.md §2/§3/§10。

### 4.2 实现包恢复与加载 ｜ D2–D3 ｜ A｜H 验收
- 做什么：写 `src/package/importer.py`，按 PACKAGE_SPEC.md §11 流程：校验扩展名/zip → spec_version 与 min_runtime → 重算 hash 比对 → 重算 permissions_digest → 权限取交集 → 加载注册 → 记恢复事件。四种拒绝情况打印明确原因。
- 验收：恢复后能跑通与导出前相同任务（用 W2 S2 场景做同任务对比）。
- 对应：PACKAGE_SPEC.md §11/§12。

### 4.3 signature.json 空壳落地 ｜ D2 ｜ A
- 做什么：按 PACKAGE_SPEC.md §9 写空壳字段（algorithm/public_key/signature/signed_at/signer 为 null，`trust=local-unverified`，`permissions_digest` 真实计算，notes 注明不实现签名逻辑）。
- 验收：结构完整，校验器读取不报错，不实现签名逻辑。

### 4.4 权限声明字段写入 ｜ D2 ｜ A
- 做什么：导出包内 tools.json 的 permissions（filesystem read/write、network enabled/allowlist、resources、forbidden_commands_inherited=true）从当前 Tools spec 与 SANDBOX_RULES 生成。
- 验收：导出包能读出「这个 Agent 声明要用什么」；`forbidden_commands_inherited=false` 的包导入时拒绝。

### 4.5 连续 10 次稳定性测试 ｜ D3–D4 ｜ A｜H 验收
- 做什么：用 S2 场景连续跑 10 次（真沙箱或 stub 计划视 Key 情况），记录崩溃/异常。
- 验收：10/10 不崩溃（PRD 成功标准 1）。

### 4.6 PRD 成功标准逐项实测并出报告 ｜ D4 ｜ A｜H 验收
- 做什么：PRD §7 共 7 条成功标准（含云端 A1' 与 Token 护栏两条 W3 新增）逐条给实测证据，未达标项写明差距；**Carry-over CO-1/CO-2/CO-3 逐条处置**（环境未到位的标注"待环境"并说明不构成 v0.1 阻断的理由）。
- 产出：`W4-acceptance.md`（验收报告）。
- 验收：每条有实测证据或明确差距说明。

### 4.7 第一版复盘文档 + v0.2 backlog 排序 ｜ D5 ｜ A｜H 验收
- 做什么：汇总 W1–W4 复盘，明确 v0.2 backlog（网页提取、PDF/Excel、定时任务、专用 Skill 容器等的实施顺序）。
- 验收：网页提取进 v0.2 顺序明确。

### 周收口 ｜ D5 ｜ A｜H 验收
- 对照下方验收清单逐条打分；出 `W4-retro.md`；v0.1 交付物清单归档。

---

## 3. 周五验收清单（H 逐条打分）

- [ ] CO-1 / CO-2 / CO-3 carry-over 逐条处置（未到位的写明差距，不静默忽略）
- [ ] 4.1 导出包 7 部分齐全、manifest 哈希正确
- [ ] 4.2 恢复后同任务跑通、四种拒绝情况明确
- [ ] 4.3 signature 空壳字段完整、不实现签名逻辑
- [ ] 4.4 权限声明可读取、forbidden_commands_inherited 校验
- [ ] 4.5 连续 10 次 10/10 不崩溃
- [ ] 4.6 PRD 七条成功标准逐项实测 + 验收报告
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
