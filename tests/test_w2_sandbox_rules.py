"""W2.3/2.4/2.5 纯逻辑可 mock 验证项（真实隔离项另见 W2-plan 真实镜像验收清单）。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from sandbox.executor import (  # noqa: E402
    MountPolicy, ResourceLimits, check_network_target,
)


# --- 2.4 挂载校验（纯逻辑） ---
def test_mount_rejects_host_root():
    p = MountPolicy(workspace_src="/")
    with pytest.raises(ValueError, match="敏感路径"):
        p.validate()


def test_mount_rejects_home_and_etc():
    for src in ("/home", "/etc", "/etc/passwd", "/proc"):
        with pytest.raises(ValueError):
            MountPolicy(workspace_src=src).validate()


def test_mount_rejects_path_traversal():
    with pytest.raises(ValueError, match="路径穿越"):
        MountPolicy(workspace_src="/data/../etc").validate()


def test_mount_rejects_oversized_scratch():
    with pytest.raises(ValueError, match="500MB"):
        MountPolicy(scratch_size_mb=501).validate()


def test_valid_mount_produces_ro_workspace_and_rw_output():
    p = MountPolicy(workspace_src="/data/in", output_src="/data/out")
    p.validate()
    mounts = p.docker_mounts()
    assert ("/data/in", "/workspace", "ro") in mounts
    assert ("/data/out", "/output", "rw") in mounts


# --- 2.3 资源参数（纯逻辑） ---
def test_resource_args_include_cpu_mem_pids_timeout_contract():
    r = ResourceLimits()
    args = r.docker_args()
    joined = " ".join(args)
    assert "--cpus" in joined and "2.0" in joined
    assert "--memory" in joined and "2048m" in joined
    assert "--pids-limit" in joined and "10" in joined
    assert r.step_timeout_s == 60
    assert r.disk_mb == 500


# --- 2.5 网络白名单（纯逻辑，可 mock 关闭） ---
def test_network_default_closed():
    allowed, reason = check_network_target("https://example.com", ())
    assert not allowed and "全断" in reason


def test_network_allowlist_exact():
    allowed, reason = check_network_target("https://api.example.com", ("api.example.com",))
    assert allowed and "白名单" in reason


def test_network_allowlist_subdomain():
    allowed, _ = check_network_target("https://sub.api.example.com", ("api.example.com",))
    assert allowed


def test_network_allowlist_rejects_other_domain():
    allowed, _ = check_network_target("https://evil.com", ("api.example.com",))
    assert not allowed


def test_network_allowlist_rejects_non_443():
    allowed, reason = check_network_target("http://api.example.com:8080", ("api.example.com",))
    assert not allowed and "443" in reason
