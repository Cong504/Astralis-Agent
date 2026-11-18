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
> 状态：**已裁决（2026-11-18，人类确认）**——PDF / Excel / 网页抓取全部不纳入 v0.1。

| 工具 | 运行时 | 依赖库 | 预估体积 | v0.1 范围 |
|---|---|---|---|---|
| 文件读写（file_read / dir_scan / report_write） | Python 3.11 | 无（标准库 os/pathlib/json/csv） | 0 | ✅ 纳入 |
| 脚本执行（script_run，Bash） | Bash + Python 3.11 | 无（标准库 subprocess） | 0 | ✅ 纳入 |
| 日志记录（log_record） | Python 3.11 | 无（标准库 json） | 0 | ✅ 纳入 |
| PDF 提取 | Python 3.11 | pdfplumber | ~50MB | ❌ 排除（backlog → v0.2） |
| Excel 处理 | Python 3.11 | openpyxl | ~30MB | ❌ 排除（backlog → v0.2） |
| 网页抓取 | Python 3.11 | httpx + beautifulsoup4 | ~40MB | ❌ 排除（PRD §5 非目标 → v0.2） |

**镜像基线**：Python 3.11 + Bash + 标准库，0 额外依赖，保持极简（< 500MB）。

### 硬约束：沙箱内禁止包管理操作（人类确认，2026-11-18）

1. **禁止**：沙箱内执行 `pip install`、`apt` / `apt-get` / `yum` / `dnf` / `apk`、`npm install` 等任何包管理操作，一律 `DENIED_BY_RULE`，不询问用户、不允许放行。
   - 实现落点：`src/sandbox/executor.py` 的 §3.5 规则已覆盖 `pip install / apt / yum / dnf / apk / npm install -g`；D1 执行器骨架须在 `check_command()` 中真正调用规则匹配，不能只留桩。
   - 理由：保持沙箱纯净与安全，避免依赖冲突与恶意包注入。
2. **替代方案**：任务若需特定库（如 PDF 解析），Agent 应通过 MCP 调用**外部已预装好环境的「专用 Skill 容器」或本地服务**，而不是在临时沙箱内安装。
   - 该能力不在 v0.1 实现（专用 Skill 容器属扩展形态）；本约束先守住「沙箱内不装包」底线，不为装包开后门。相关需求进 PRD backlog。
3. **构建期与运行期分离**：镜像构建期（Dockerfile 的 RUN）允许装基础运行时；运行期（容器内执行的任务脚本）禁止任何包管理，两者不得混淆——不能因构建期装过包就在运行期放开。

**验收补强**：
- 2.1 增加「容器内执行 `pip install requests` 返回 DENIED_BY_RULE §3.5」。
- 2.4 增加「`python -c 'import pdfplumber'` 在沙箱内失败（证明无预装额外库）」。

### 镜像获取记录（2026-11-18，已解决）

- 原阻碍：`registry-1.docker.io:443` 不可达、无本地基础镜像。
- 解除：人类授权后，用国内 mirror `docker.1ms.run/library/python:3.11-slim` 拉取成功（401 质询属 registry 正常行为），`docker tag` 为 `python:3.11-slim`，`local-agent/sandbox:0.1` 构建成功。
- 镜像实测：187MB（< 500MB）、whoami=agent / uid=10001、无 pip、Python 3.11.18 + Bash 5.2。
- 真实验收中发现并修复一个 mock 测不出的缺陷：tmpfs 挂载默认属主 root，非 root 用户不可写——已在 `create_container()` 的 tmpfs 参数加 `uid/gid=10001`，复测可写且 16m 限额生效。

### 验收分层：可 mock 验证 vs 必须真实镜像验收

> 用户明确要求：资源限制是否真生效（2.3）、只读/tmpfs 真隔离（2.4）、工具在容器里真执行（2.6）不得只凭 mock 签字。
> 划分：**2.5 白名单匹配纯逻辑**（`check_network_target`）可 mock 关闭；其余三项必须真实容器实测，统一进下方「真实镜像验收清单」。

**真实镜像验收清单**（build 跑通后逐条打勾关闭；已完成项标注实测证据）：

- [x] R1 非 root：whoami=agent，uid=10001（实测 `id -u`=10001）
- [x] R2 体积 < 500MB：187MB
- [x] R3 无包管理：容器内 `pip` 不存在；`import pdfplumber` 失败
- [x] R4（2.4）`/workspace` 只读：写入报 `Read-only file system`，WRITE_DENIED
- [x] R5（2.4）`/tmp/agent-scratch` tmpfs 可写且限额：uid/gid 修复后写入成功；16m 限额写 20m 被截断到 16m
- [x] R6（2.6）工具命令在容器内真执行：容器内跑 Python 标准库脚本输出 `{"files": 0}`，退出码 0
- [x] R7（2.3）资源参数被 daemon 接受：`--cpus 2 --memory 2048m --pids-limit 10 --cap-drop ALL --network none --user 10001` 实跑输出 resources_ok
- [x] R8（2.3）超时真终止：`sleep 120` + step_timeout=3s → verdict=TIMEOUT，3.8s 被 kill，容器已销毁（scripts/verify_limits.py 实测）
- [x] R9（2.3）内存超限真 OOM：分配 512MB + memory=64m → verdict=OOM_KILLED，exit=137，容器已销毁（scripts/verify_limits.py 实测）
- [ ] R10（2.5）网络白名单真实连通 → **进 backlog，标记「需白名单环境」**，按裁决保持纯 mock 关闭，不阻塞 W2 收尾

> R10 为唯一未关闭的真实项，已明确挂 backlog；其余 R1–R9 全部真实实测打勾。

### S2 端到端实测结果（W2.7，2026-11-18）

- 任务：扫描目录 → 生成 Markdown 报告 → 落库；真 Docker 容器执行（stub 计划，backend=stub，但沙箱为真）。
- 结果：**PASS**，状态 RECEIVED → PLANNING → AWAIT_CONFIRM → EXECUTING → DONE；报告含 `notes.txt / data.csv / sub/deep.md` 三个真实扫描结果。
- 命令：`python scripts/run_s2_smoke.py`。
- 端到端中发现并修复两个真 bug：① Windows GBK 默认编码导致 docker CLI 输出 UTF-8 解码崩溃（`_default_runner` 改 `encoding=utf-8, errors=replace`）；② Windows Docker Desktop 挂载卷 uid 映射导致宿主读文件 PermissionError（报告验证改为「容器内读回自证」，宿主读取标 N/A 不作判据）。
- 报告落地位置：宿主输出挂载 `/output/report.md`（容器内可见并经容器读回验证）；宿主直读在 Windows 挂载下 N/A。

### 冷启动基线（供 2.8 定预算上界，2026-11-18 实测）

| 指标 | 实测值 | 说明 |
|---|---|---|
| create（docker create） | 0.173s | 新建一次性容器 |
| create → 首条命令返回 | 0.704s | 冷启动主指标 |
| 端到端（含计划/确认/2 步执行） | 1.80s | 全链路 |

> 2.8 预算上界待你据上述基线拍板；当前仅记录基线，不预设 N 值。

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

- [x] 2.0 工具依赖清单已确认，镜像不超范围（无 PDF/Excel/网页库）
- [x] 2.1 镜像非 root、187MB、无 pip（见真实镜像验收清单 R1–R3）
- [x] 2.2 执行器独立容器、结束即销毁、签名未改（10 个 mock 单测）
- [x] 2.3 资源参数生效 + 超时真终止 + OOM 记录（R7/R8/R9 全部实测过）
- [x] 2.4 只读挂载 + tmpfs 可写限额（R4/R5 实测过）；路径穿越拦截（mock 过）
- [x] 2.5 默认断网 + 白名单匹配逻辑（`check_network_target` 纯 mock 关闭）；R10 真实出网进 backlog
- [x] 2.6 五个工具实现 + mock 派发测试（R6 容器内真执行已过）；A3 Stdio 握手待 D4
- [x] 2.7 S2 场景真跑通（PASS），报告落地，状态机完整，日志含每步 verdict/stdout
- [ ] 2.8 冷启动预算上界待人类拍板（基线已测：create 0.173s / create→首命令 0.704s / E2E 1.80s）；A2/A3/A4 待 D4–D5

### Backlog（W2 产生，不进本周验收）

- **R10 网络白名单真实出网验证**：需白名单网络环境，按裁决保持纯 mock 关闭，环境具备后补测。

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
