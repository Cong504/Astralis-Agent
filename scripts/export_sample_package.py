"""导出 W4 样例 Agent 包（4.1 验收产物）。

用 W2 的 S2 场景配置导出一个真实 .agentpkg 到 packages/，供 4.2 恢复测试复用。
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from compat.encoding import force_utf8_stdio  # noqa: E402

force_utf8_stdio()
from package.exporter import AgentSpec, export_package  # noqa: E402


def main() -> int:
    tools = {
        "protocol": "mcp",
        "tools": [
            {"name": "dir_scan", "source": "builtin"},
            {"name": "file_read", "source": "builtin"},
            {"name": "report_write", "source": "builtin"},
            {"name": "log_record", "source": "builtin"},
        ],
        "permissions": {
            "filesystem": {"read": ["/workspace"], "write": ["/tmp/agent-scratch", "/output"]},
            "network": {"enabled": False, "allowlist": []},
            "resources": {"cpu": 2, "memory_mb": 2048, "disk_mb": 500, "step_timeout_sec": 60},
            "forbidden_commands_inherited": True,
        },
    }
    spec = AgentSpec(
        name="s2-file-triage",
        prompt="# 系统提示词\n扫描 /workspace 下的目录，统计文件类型分布并生成 Markdown 报告。\n"
               "安全边界由执行层规则保证，本提示词不声明任何绕过。",
        tools=tools,
        config={"model": {"provider": "deepseek", "primary": "deepseek-chat"},
                "sandbox": {"policy": "default", "image": "local-agent/sandbox:0.1"},
                "runtime": {"confirm_before_execute": True, "auto_reflect": True}},
        skills={"scan_directory": "name: scan_directory\ndescription: 扫描目录并统计\n"
                                  "risk_level: low\nrequires_confirm: false\n"},
        memory={"schema": "experience.v1", "items": []},
        description="S2 本地文件扫描与报告生成（v0.1 样例）",
    )
    pkg = export_package(spec, ROOT / "packages")
    print(f"[export] 包路径: {pkg}")
    with zipfile.ZipFile(pkg) as zf:
        names = zf.namelist()
        print(f"[export] 文件数: {len(names)}")
        for n in sorted(names):
            print(f"  {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
