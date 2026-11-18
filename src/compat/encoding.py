"""全局 UTF-8 stdio 防御（W3 复盘系统级方案）。

背景：W2 抓到 Docker stdout 的 GBK 解码崩溃（个案修复：_default_runner 加 encoding），
W3 又踩到控制台 print 的 GBK 崩溃（verify_cost_guard 的 ¥ 字符）——
「记住教训 + 当场修复」无效，教训未转化为系统防御就会换马甲重演。

本模块把「个案修复」升级为「全局强制」：所有入口（CLI / scripts / tests）
启动时调用 force_utf8_stdio()，一次性把 stdout/stderr 钉死为 UTF-8，
并设置 PYTHONIOENCODING 子进程继承。之后新增脚本/模块无需各自记得处理编码。

幂等：可重复调用，不重复 reconfigure。
"""

from __future__ import annotations

import os
import sys

_FORCED = False


def force_utf8_stdio() -> None:
    """把本进程 stdio 强制为 UTF-8（errors=replace 兜底），并让子进程继承。"""
    global _FORCED
    if _FORCED:
        return
    os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass  # 已重定向/不支持 reconfigure 的流（如 pytest capture）跳过
    _FORCED = True
