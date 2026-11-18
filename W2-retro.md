# W2-retro.md — 第 2 周周报与复盘

> 版本：v0.1 ｜ 状态：已归档（W2 正式收口，2026-11-18）｜ 关系：依据 W2-plan.md 验收清单逐条打分；复盘归因见下；带入项进 W3-plan.md
> 写作依据：AGENTS.md §7「每周周报 + 复盘，哪里失败、为什么、下次怎么改」；验收标准来自 DEVELOPMENT_PLAN.md §W2。

---

## 1. 周五打分（对照 W2-plan §4 验收清单）

| 编号 | 验收项 | 打分 | 证据 |
|---|---|---|---|
| 2.0 | 工具依赖清单确认，无 PDF/Excel/网页库 | 通过 | W2-plan §1 已裁决；镜像 0 额外依赖 |
| 2.1 | 镜像非 root、< 500MB、无 pip | 通过 | R1–R3：whoami=agent / uid=10001 / 187MB / 无 pip |
| 2.2 | 执行器独立容器、结束即销毁、签名未改 | 通过 | 10 个 mock 单测 + 真跑 R8/R9 均验证容器销毁 |
| 2.3 | 资源参数生效 + 超时真终止 + OOM 记录 | 通过 | R7/R8/R9：参数被 daemon 接受；3.8s kill；exit=137 OOM_KILLED |
| 2.4 | 只读挂载 + tmpfs 可写限额；路径穿越拦截 | 通过 | R4/R5 真实验收；路径穿越 mock 通过 |
| 2.5 | 默认断网 + 白名单匹配逻辑 | **部分通过** | 匹配逻辑纯 mock 关闭；R10 真实出网进 backlog（需白名单环境） |
| 2.6 | 五个工具实现 + A3 握手 | 通过 | R6 容器内真执行；A3 Stdio 握手 0.088s PASS |
| 2.7 | S2 场景真跑通 | 通过 | 端到端 PASS，状态机完整，报告含真实扫描结果 |
| 2.7′ | CLI 真执行接线（backend 非 stub） | 通过 | `agent run --real` task-2bb067de：backend=deepseek，真沙箱，落库真扫描 |
| 2.8 | 冷启动 p95 ≤ N=1.0s | **部分通过** | warm p95=0.7002s 通过；cold-after-pull 本机无法复现，挂 W3-plan W2-C1 |
| A1' | 10 次合法率 | 通过 | raw 10/10，降级 0/10，原始样本落盘 |
| A2/A3/A4 | 三项硬结论 | 通过 | 均有书面结论 + 原始数据 |

**未完全关闭项**（均非失败，是环境受限的诚实延期）：R10 真实出网、cold-after-pull 样本。两项都已写入 W3-plan 带入项首行，触发条件明确，不计为"连续 2 周未通过"（AGENTS.md §7 砍需求触发条件未达）。

---

## 2. 三个真 bug 归因

> 共同特征：三个都是**集成层错**——mock 全绿、逻辑正确、设计无误，只有真实运行环境才暴露。全部发生在 D2–D3（原则固化之前）。

### Bug 1：tmpfs 属主导致非 root 不可写（D2，2.4 真实验收抓到）

- **现象**：真实容器里 `--tmpfs /tmp/agent-scratch:rw,size=16m`，uid 10001 写文件 `Permission denied`；mock 单测全绿。
- **根因**：tmpfs 挂载默认属主是 root，与镜像里的非 root 用户无关。执行器构造 tmpfs 参数时只给了 `rw,size=`，没给 `uid/gid`。
- **为什么 mock 漏掉**：mock runner 返回假 `CompletedProcess`，不真的挂载文件系统，权限语义根本不存在。
- **归因分类**：**权限**（挂载卷属主）；也涉及生命周期（容器按此配置创建）。
- **修复**：`create_container()` 的 tmpfs 参数加 `uid=10001,gid=10001`（取 `request.limits.non_root_uid`），复测可写且 16m 限额生效。
- **教训**：任何"非 root + 可写挂载"的组合，必须在真容器里写一次，不能靠参数字符串正确就签字。

### Bug 2：GBK 解码崩溃（D3，S2 端到端抓到）

- **现象**：CLI 真执行时后台线程 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xae`；stdout 变 None，验证脚本 TypeError。
- **根因**：Windows 上 `subprocess.run(..., text=True)` 用 locale 默认编码（GBK）解码 docker CLI 的 UTF-8 输出（报告含中文 `# 目录扫描报告`）。
- **为什么 mock 漏掉**：mock runner 直接返回 Python `str`，不经过 subprocess 的字节→字符解码环节；编码边界完全没被 exercised。
- **归因分类**：**编码**（宿主 locale vs 容器输出）+ **I/O**（stdout 采集路径）。
- **修复**：`_default_runner` 加 `encoding="utf-8", errors="replace"`；同类模式同步到 stdio_bridge 与 verify_limits。
- **教训**：只要 stdout/stderr 跨进程且内容可能含非 ASCII，必须显式指定编码；"text=True"不是编码安全的同义词。

### Bug 3：挂载卷 uid 映射导致宿主读文件 PermissionError（D3，S2 端到端抓到）

- **现象**：报告文件确实生成（容器内 `cat` 可见、状态 DONE），但宿主 Python `read_text()` 抛 `PermissionError [Errno 13]`。
- **根因**：Windows Docker Desktop 的挂载卷 uid 映射——容器内 uid 10001 创建的文件，宿主当前用户无读权限；`chmod 666`（容器内视角）不改变宿主看到的 ACL 语义。
- **为什么 mock 漏掉**：mock 不产生真实文件，宿主/容器双视角的权限差异不存在。
- **归因分类**：**权限**（跨平台 uid 映射）+ **I/O**（挂载卷落地与读回）。
- **修复**：① 报告写入加 `chmod 666` 尽量放开；② 验收判据改为「容器内读回自证」（`cat /output/report.md` 经 stdout 返回并校验内容），宿主直读标 N/A 不作判据——因为这不是代码 bug，是 Windows 挂载的固有行为。
- **教训**：跨平台文件落地，必须区分"容器内可见"与"宿主可读"两个判据；不能把平台限制误判为功能失败，也不能为让宿主可读而破坏非 root 边界。

---

## 3. 「I/O / 编码 / 权限 / 生命周期必须实测」原则的拦截效果验证

**必须先说清因果关系（不夸大）**：这条原则是在 D4 才写进 W2-plan 的，三个 bug 发生在 D2–D3。因此三个 bug 是**催生这条原则的来源证据**，不是"被这条原则提前拦截的战果"。把因果说反，就是把事后总结伪装成事前预见。

**原则固化后的实际效果（D4）**：

| 验证动作 | 是否真实 | 结果 |
|---|---|---|
| A1' 10 次真实 DeepSeek 调用 | 真 API | 抓到过 prompt 未列白名单时的自由 tool 名/数字 step_id（模型输出方差），促成 prompt 根因修复；修复后 raw 10/10 |
| CLI --real 端到端 | 真云端 + 真容器 | 确认 backend=deepseek 非 stub、落库真扫描结果；未再出现"mock 绿、真实挂"的静默漏过 |
| 冷启动压测 20 样本 | 真容器 | p95 原始样本落盘，非估算 |
| A3 Stdio 握手 | 真子进程 | 进程间往返实测 |

**结论**：原则固化后，本轮所有涉及 I/O/编码/权限/生命周期的验收均走了真实环境，没有再发生静默漏过；A1' 的真实调用还主动抓到了"模型输出方差"这类 mock 永远看不到的问题。原则是有效的，但它的有效性来自"强制执行真实验证"这个动作，不是来自写下文字本身。

---

## 4. 哪里失败 / 为什么 / 下次怎么改

| 项 | 内容 |
|---|---|
| 失败点 | 三个集成层 bug（权限×2、编码×1）都在真容器才暴露；模型输出方差曾在单点验证时几乎被掩盖 |
| 为什么 | ① W2 早期对"真实项"的边界划得不够细，把参数正确等同于运行正确；② Windows 宿主 + Linux 容器 + 非 root 的组合有平台特有语义，mock 无法表达；③ A1' 初版只有单点证据，差点以"一次成功"结案 |
| 下次怎么改 | ① 把原则写进每份 plan 的验收清单而非只写一次（已完成：W2-plan §0）；② 真实项验收必须附实测命令与原始输出，否则不打勾；③ A1' 升级为回归门禁——改 prompt 或扩工具集必重跑，纳入 CI（已写进 W3-plan） |
| 做对了什么 | 每次真 bug 都当场修复并复测，没有用 mock 绿掩盖；R10/cold 不伪造数据、明确挂带入项；A1' 诚实区分"曾跑偏"与"prompt 收敛后 10/10" |

---

## 5. W2 交付物清单

- 代码：执行器骨架 + 资源采集 + 挂载校验 + 白名单逻辑 + 5 个工具 + Runtime 真执行模式 + CLI --real + 归一化 + Stdio 桥接
- 镜像：`local-agent/sandbox:0.1`（187MB，非 root，无包管理）
- 文档：`W2-plan.md`（含真实验收清单 R1–R10 + 原则）、`ADR-0001`、`ADR-0003`
- 证据：`logs/bench_cold_start_warm.json`、`logs/bench_a1_legality.json`、本报告 `W2-retro.md`
- 测试：77 passed，lint 无错误
- 遗留：W2-C1（cold 样本）、W2-R10（白名单出网）——已写入 W3-plan 带入项
