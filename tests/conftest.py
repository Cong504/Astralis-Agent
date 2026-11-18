"""pytest 全局配置：会话启动即强制 UTF-8 stdio（W3 系统级编码防御）。

所有测试在 UTF-8 下运行，避免 Windows GBK 让含 ¥/中文的输出测试崩溃。
个案修复不再依赖每个脚本各自记得 reconfigure。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from compat.encoding import force_utf8_stdio  # noqa: E402

force_utf8_stdio()
