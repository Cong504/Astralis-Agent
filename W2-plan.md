# W2 plan.md — 第 2 周：沙箱与工具（接入真执行）

> 版本：v0.1 ｜ 周期：第 2 周（D1–D5）｜ 状态：**待人类确认**（AGENTS.md §6 门禁：经确认后才开工）
> 上游依据：DEVELOPMENT_PLAN.md §W2（2.0–2.8）、ARCHITECTURE.md §2/§5/§8、SANDBOX_RULES.md §2–§7、ADR-0001/ADR-0003、src/sandbox/executor.py 与 src/tools/mcp_bridge.py（W2 预备接口已定）
> 路线：云端先行（DeepSeek 主用）——模型调用复用 W1 已落地的 router.py 与 Runtime Token 预算，W2 不重复造轮子。

---

## 0. 本周唯一目标

把 W1 的执行桩换成真执行：计划 → 工具 → Docker 沙箱 真跑起来，结果落库；完成 S2 场景「扫描目录并生成 Markdown 报告」端到端。

- 本周首次引入真 Docker 容器，安全边界（SANDBOX_RULES.md）必须同步到位，不能先跑后补。
- W1 定的五段管道接口与沙箱/工具接口签名本周只填空、不改签名（ADR-0001 约束）。
- 模型全走云端；本地 14B 不参与（ADR-0003，`deploy_14b.sh` 已 DEFERRED）。

分工：你负责收敛与验收签字；开发 Agent 按本作业单实现。

---

## 1. W2-2.0 前置：工具依赖清单（先清单后镜像）

> 目的：你指出的坑——先定依赖再定镜像，避免镜像做完发现缺库返工。

| 工具 | 运行时 | 依赖库 | 预估体积 |
|---|---|---|---|
| 文件读写（file_read / dir_scan / report_write） | Python 3.11 | 无（标准库 os/pathlib/json/csv） | 0 |
| 脚本执行（script_run，Bash） | Bash + Python 3.11 | 无（标准库 subprocess） | 0 |
| PDF 提取 | Python 3.11 | pdfplumber | ~50MB |
| Excel 处理 | Python 3.11 | openpyxl | ~30MB |
| 网页抓取 | Python 3.11 | httpx + beautifulsoup4 | ~40MB |

**结论与冲突提示（需你裁决）**：

1. 上述后三项（PDF / Excel / 网页抓取）**与 PRD.md §5 非目标冲突**——「网页信息提取排入第二版（v0.2）」，且 S2 场景（本地文件处理）只需前两项。因此 **W2 镜像按「前两项」定基础依赖（0 额外库）**，后三项不进 W2 镜像。
2. 若你确认要把 PDF/Excel 纳入 v0.1，须先改 PRD 边界（新增需求走 backlog 决议），再重做依赖清单与镜像；这属于扩范围，我不擅自决定。
3. W2 的 5 个基础工具按 DEVELOPMENT_PLAN.md 2.6 固定为：`file_read / dir_scan / script_run / report_write / log_record`——均落在「无额外依赖」区间，镜像可保持 < 500MB。

---

## 2. 任务分解（2.1–2.8）

> 负责列：A = 开发 Agent 实现，H = 人类决策/验收。

### 2.1 构建沙箱基础镜像（W2-2.0 的清单落地产出）｜ D1 ｜ A｜H 验收
- 做什么：写 `docker/sandbox.Dockerfile`——非 root（uid 10001）、最小依赖、`/workspace` 只读挂载点、`/tmp/agent-scratch` tmpfs 可写、`--cap-drop ALL`；按 §1 清单只装 Python 3.11 标准库，不预装 PDF/Excel/网页库。
- 产出：`docker/sandbox.Dockerfile`、`scripts/build_sandbox.sh`、镜像 `local-agent/sandbox:0.1`。
- 验收：`whoami` 非 root；镜像 < 500MB；容器内无 `curl/wget/nc/sudo`。
- 对应：SANDBOX_RULES.md §2/§5/§6。

### 2.2 实现沙箱执行器（填空 executor.py）｜ D1–D2 ｜ A
- 做什么：实现 `SandboxExecutor` 的 `create_container/run/collect/destroy`，复用已定的 `execute()` 模板；每次执行独立容器、结束即销毁。
- 产出：`src/sandbox/docker_executor.py` + 单测（用 mock Docker API，不依赖真 daemon）。
- 验收：执行后容器不存在；timeout 路径先 kill 再记 TIMEOUT。
- 接口约束：`src/sandbox/executor.py` 签名不改（ADR-0001）。

### 2.3 资源限制落地｜ D2 ｜ A｜H 验收
- 做什么：`ResourceLimits.docker_args()` 接入 `docker run`；磁盘配额用 `--storage-opt size=500m`（若 daemon 不支持则记录 ADR 并改用挂载配额）。
- 验收：单步超 60s 被终止且日志有 TIMEOUT；内存超限 OOM 被记录。
- 对应：SANDBOX_RULES.md §2。

### 2.4 文件系统挂载策略｜ D2 ｜ A
- 做什么：`MountPolicy.docker_mounts()` 接入；`/workspace` 只读、`/tmp/agent-scratch` 可写 ≤500MB、输出目录仅确认后可写挂载；路径穿越拦截。
- 验收：容器内写 `/workspace` 必然失败；`../../etc/passwd` 被拒。
- 对应：SANDBOX_RULES.md §5。

### 2.5 网络默认断开 + 白名单｜ D3 ｜ A｜H 验收
- 做什么：`--network=none` 默认；白名单逐域名配置且只放出站 443；模型 API 不在容器内发起（Key 不进容器）。
- 验收：默认无法出网；白名单域名可访问。
- 对应：SANDBOX_RULES.md §6。

### 2.6 实现 5 个基础工具（填空 mcp_bridge.py）｜ D3–D4 ｜ A
- 做什么：按 §1「无额外依赖」区间实现 `file_read / dir_scan / script_run / report_write / log_record`；`StdioMCPBridge` 先做 A3 握手验证（initialize→tools/list→tools/call），若 Stdio 集成阻塞 >1 天则回退自定义插件接口并出 ADR。
- 产出：`src/tools/builtin.py`、`src/tools/stdio_bridge.py` + 单测。
- 验收：5 个工具全部可调用；高危命令由执行层拒绝（不依赖提示词）。
- 对应：SANDBOX_RULES.md §3/§4、PACKAGE_SPEC.md §6。

### 2.7 全链路打通（S2 场景）｜ D4–D5 ｜ A｜H 验收
- 做什么：Runtime 五段管道 execute 段接真沙箱；跑通「扫描目录 → 统计 → 生成 Markdown 报告」；报告落地 `/tmp/agent-scratch` 并经确认后拷出。
- 验收：报告文件真实生成；日志含每次工具调用、退出码、usage、判定结果。
- 模型：云端 DeepSeek（无 Key 时 stub 标注）。

### 2.8 冷启动性能压测 + 周收口｜ D5 ｜ A｜H 验收
- 做什么：容器创建耗时采样；出 ADR（如超标）；周五对照下方验收清单逐条打分。
- 验收：容器创建 < 2s；未过项写明差距与处置。

---

## 3. 本周硬结论（必须出书面）

| 编号 | 假设 | 测试方式 | 成功信号 | 失败动作 |
|---|---|---|---|---|
| A2 | 五段管道接口能覆盖 W2 真执行 | 填 execute 段不改接口签名 | 桩→真执行无接口改动 | 本周内调整接口，不留到 W3 |
| A3 | MCP Stdio 能承载工具接入 | echo 握手一次进程间调用 | 完成 initialize→call 往返 | 回退自定义插件接口 → 出 ADR |
| A4 | Docker 新建容器开销可接受 | 20 次冷启动采样 | 平均 < 2s | 预热镜像 / 精简基础镜像 → 出 ADR |

> 注：原 A1（本地 14B 合法率）已按 ADR-0003 取消；云端 A1'（DeepSeek ≥8/10 合法、p95<30s）待你注入 Key 后在 W1/W2 边界实测，当前无 Key 只能跑 stub。

---

## 4. 周五验收清单（H 逐条打分）

- [ ] 2.0 工具依赖清单已确认，镜像不超范围（无 PDF/Excel/网页库）
- [ ] 2.1 镜像非 root、< 500MB、无高危网络工具
- [ ] 2.2 执行器独立容器、结束即销毁、签名未改
- [ ] 2.3 资源限制生效（60s 超时、OOM 记录）
- [ ] 2.4 只读挂载 + 路径穿越拦截
- [ ] 2.5 默认断网 + 白名单可用
- [ ] 2.6 五个工具全部可调用，A3 有结论
- [ ] 2.7 S2 场景真跑通，报告落地，日志完整
- [ ] 2.8 冷启动 < 2s，A2/A3/A4 均有书面结论

---

## 5. 本周明确不做（防底座做太厚）

- 不做 PDF / Excel / 网页抓取工具（PRD §5 非目标，除非你确认扩范围）
- 不做 W3 的高危命令真实拦截套件（本周只确保执行层调用 executor.FORBIDDEN_RULES，完整对抗测试在 W3）
- 不做复盘生成、记忆层、Agent 包导出（W3/W4）
- 不做本地模型部署（`deploy_14b.sh` DEFERRED，ADR-0003）
- 不做云端自动互备（Qwen 仅手动切换，ADR-0003）

---

## 6. 风险与砍范围预案

| 触发 | 处置 |
|---|---|
| 工具依赖清单确认后镜像超 500MB | 砍非核心工具到 W3，先保 S2 闭环 |
| Stdio 集成阻塞 >1 天 | 回退自定义插件接口，出 ADR，不改变工具签名 |
| Docker daemon 不可用（当前机已 E06 通过，风险低） | 在具备 Docker 的机器上执行，代码用 mock 单测兜底 |
| 云端无 Key | 保持 stub + `backend=stub` 标注，S2 用 stub 计划跑真沙箱 |
| 任何新想法（记忆层/Warm 层/PDF 工具） | 一律进 PRD backlog，不进本周任务表 |

---

## 7. 需要你确认的两件事（开工门禁）

1. **§1 冲突裁决**：PDF/Excel/网页抓取是否纳入 v0.1？我的建议按 PRD 非目标保持不纳入（镜像 0 额外依赖）；若纳入须先改 PRD 边界并出 backlog 决议。
2. **本作业单是否确认**：确认后开发 Agent 从 2.0→2.1 开始，D1 即产出基础镜像与执行器骨架。
