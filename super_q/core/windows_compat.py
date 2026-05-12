"""Windows 启动兼容性处理。"""

import os
import platform


def patch_slow_platform_machine() -> None:
    """避免 Python 3.14 在 Windows 上通过 WMI 获取 machine 时卡住。"""
    if os.name != "nt":
        return
    arch = os.environ.get("PROCESSOR_ARCHITECTURE") or "AMD64"
    platform.machine = lambda: arch
