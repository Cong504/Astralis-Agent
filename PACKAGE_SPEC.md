# PACKAGE_SPEC.md — Agent 包格式规范 v0.1

> 版本：v0.1 ｜ 状态：已确认 ｜ 范围：**第一版只做本地导出与恢复**，签名逻辑不实现但结构必须定稿
> 设计原则：格式先为「可信交换」预留空间，能力可以晚到，字段不能后补（后补即破坏兼容）。

---

## 1. 为什么包格式要从第一天定好

一个 Agent 包**不是普通文件，而是一个可执行体**。别人导入你的包，等于允许一段未知逻辑在他的环境里调用工具、读写文件、执行命令。

因此即使 v0.1 不做交换，也必须先把**权限声明**和**签名字段**写进格式里。这样未来开放交换时，格式不用改，只需补实现。

---

## 2. 包目录结构（强制）

```
agent-package/
├── manifest.json        # 必需｜元数据与完整性
├── prompt.md            # 必需｜系统提示词
├── skills/              # 必需（可为空目录）｜技能定义
│   └── <skill_name>.yaml
├── tools.json           # 必需｜绑定工具与权限声明
├── config.json          # 必需｜模型配置 + 沙箱配置引用
├── memory/              # 可选｜经验数据
│   └── experiences.json
└── signature.json       # 必需｜权限声明 + 签名（v0.1 允许字段为空值）
```

**打包格式**：目录整体压缩为 `<agent_name>-v<version>.agentpkg`（zip 容器，扩展名自定义）。

---

## 3. manifest.json

```json
{
  "spec_version": "0.1",
  "name": "file-triage-agent",
  "version": "1.0.0",
  "author": "local",
  "created_at": "2026-11-17T16:00:00+08:00",
  "description": "本地文件扫描与报告生成",
  "hash": {
    "algorithm": "sha256",
    "value": "<除 signature.json 外全部文件的聚合哈希>"
  },
  "entrypoint": "prompt.md",
  "min_runtime": "0.1.0",
  "capabilities_required": ["dir_scan", "file_read", "report_write"]
}
```

字段规则：
- `version` 用语义化版本（MAJOR.MINOR.PATCH）；**MAJOR 递增代表包结构不向后兼容**
- `hash.value` 计算时排除 `signature.json` 自身（避免自引用）
- `min_runtime` 必填：恢复时运行时版本低于此值 → 拒绝加载并说明原因
- `spec_version` 与运行时支持的版本不匹配 → 拒绝加载

---

## 4. prompt.md

系统提示词正文。规则：
- 纯 Markdown，UTF-8，不含密钥、不含绝对路径、不含机器专属配置
- 不得写入「忽略安全规则」一类试图越过执行层约束的指令（这类约束本身无效，安全在 SANDBOX_RULES.md 的执行层）

---

## 5. skills/<name>.yaml

单个技能定义。固定字段：

```yaml
name: scan_directory
description: 扫描指定目录并统计文件类型分布
inputs:
  - { name: path, type: string, required: true, note: "必须位于 /workspace 之下" }
steps:
  - action: tool_call
    tool: dir_scan
    args: { path: "{{inputs.path}}" }
  - action: tool_call
    tool: report_write
    args: { target: "/tmp/agent-scratch/report.md" }
risk_level: low            # low | medium | high
requires_confirm: false     # risk_level=high 时此项必须为 true
```

字段规则：
- `risk_level: high` ⇒ `requires_confirm` **必须为 true**，加载时校验，违反即拒绝导入
- `inputs[].note` 中的路径约束会进权限声明（见 §6）

---

## 6. tools.json（含权限声明）

```json
{
  "protocol": "mcp",
  "tools": [
    { "name": "dir_scan", "source": "builtin" },
    { "name": "file_read", "source": "builtin" },
    { "name": "report_write", "source": "builtin" }
  ],
  "permissions": {
    "filesystem": {
      "read":  ["/workspace"],
      "write": ["/tmp/agent-scratch"]
    },
    "network": {
      "enabled": false,
      "allowlist": []
    },
    "resources": {
      "cpu": 2, "memory_mb": 2048, "disk_mb": 500,
      "step_timeout_sec": 60
    },
    "forbidden_commands_inherited": true
  }
}
```

权限声明规则：
- 声明是**上限**，实际授予永远取「声明 ∩ 本地策略」的交集；本地可再收紧，不可放宽
- `forbidden_commands_inherited: true` 表示必须继承 SANDBOX_RULES.md §3 全部禁止清单；**设为 false 的包一律拒绝加载**
- `network.enabled: true` 的包在 v0.1 只允许导出/导入到同一台机器，跨机导入需人工确认

---

## 7. config.json

```json
{
  "model": {
    "primary": "local:qwen2.5-14b-instruct",
    "fallback": "cloud:<已备案模型>",
    "max_output_tokens": 2048,
    "single_task_token_cap": 50000
  },
  "sandbox": { "policy": "default", "image": "local-agent/sandbox:0.1" },
  "runtime": { "confirm_before_execute": true, "auto_reflect": true }
}
```

- 不写任何密钥明文。凭据引用本地环境，例如 `api_key_ref: "env:CLOUD_MODEL_KEY"`
- `single_task_token_cap` 用于成本护栏，缺失时运行时按默认上限兜底

---

## 8. memory/experiences.json（可选）

```json
{
  "schema": "experience.v1",
  "items": [
    {
      "id": "exp-0001",
      "task_pattern": "directory scan with many nested folders",
      "outcome": "success",
      "note": "先限制递归深度再统计，避免超时",
      "created_at": "2026-11-20T10:00:00+08:00"
    }
  ]
}
```

- v0.1 只存结构化经验条目 + 标签，**不做语义检索、不存向量**
- 导出时默认包含经验数据；如涉敏感路径，导出时可用 `--exclude-memory` 排除

---

## 9. signature.json（v0.1 空壳，字段定稿）

```json
{
  "algorithm": null,
  "public_key": null,
  "signature": null,
  "signed_at": null,
  "signer": null,
  "permissions_digest": "sha256:<tools.json.permissions 的哈希>",
  "trust": "local-unverified",
  "notes": "v0.1 不实现签名逻辑，仅预留字段与校验入口"
}
```

- `permissions_digest` **v0.1 就要真实计算**，它是未来做「权限变更可追溯」的锚点
- `trust` 取值：`local-unverified` | `self-signed` | `trusted-author`（后两值 v0.1 不产生）
- 运行时必须能读取本文件且不报错（这是 §4.3 的验收标准）

---

## 10. 导出流程

```
1. 收集 Agent 实例的 prompt / skills / tools / config / memory
2. 计算 hash.value（排除 signature.json）
3. 计算 permissions_digest 写入 signature.json
4. 写入 manifest.json（version 与 min_runtime 校验）
5. 结构校验：文件齐备、JSON 可解析、yaml 可解析、risk_level 与 requires_confirm 一致
6. 压缩为 <name>-v<version>.agentpkg，放入 packages/
```

导出失败必须回报具体是哪一步、哪个文件、哪个字段不合规，不允许静默产出一个坏包。

## 11. 恢复流程

```
1. 校验扩展名与 zip 完整性
2. 读 manifest.json → 检查 spec_version 与 min_runtime 是否被当前运行时支持
3. 重算 hash 并与 manifest 比对 → 不一致视为包已损坏或被篡改，拒绝加载
4. 重算 permissions_digest 与 signature.json 比对 → 不一致拒绝加载
5. 加载 tools.json → 计算「声明 ∩ 本地策略」得到实际授予集
6. 加载 prompt / skills / config / memory，注册为可运行 Agent
7. 记录一次恢复事件到审计日志
```

**拒绝加载的四种情况**（必须打印明确原因，不允许模糊报错）：
1. 完整性校验失败（哈希不一致）
2. spec_version 不兼容
3. 声明了 `forbidden_commands_inherited: false`
4. `risk_level: high` 但 `requires_confirm: false`

## 12. 版本兼容策略

| 变更类型 | 递增位 | 兼容要求 |
|---|---|---|
| 新增可选字段 | PATCH | 旧包可被新运行时加载 |
| 新增必需字段 | MINOR | 新运行时需提供默认值兼容旧包 |
| 字段改名/删除/语义变化 | MAJOR | 明确拒绝加载，不做静默猜测 |

## 13. 分阶段路线（交换能力）

| 阶段 | 内容 | 版本 |
|---|---|---|
| 第一步 | 本地导出 / 恢复 / 备份，不联网分享 | **v0.1（当前）** |
| 第二步 | 小范围可信交换：导入前沙箱试运行 + 权限审查报告 + 行为审计 | v0.4 |
| 第三步 | 公开市场：作者认证、安全评分、版本管理、分成 | 远期，不排期 |

第二步的前置条件：v0.1 的包格式（本文件）已经定稿且经过 ≥10 个包的实际导出/恢复验证。
