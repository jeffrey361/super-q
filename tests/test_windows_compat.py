"""Windows 兼容性处理测试。"""

import platform

from super_q.core.windows_compat import patch_slow_platform_machine


def test_patch_slow_platform_machine_replaces_platform_machine(monkeypatch) -> None:
    """启动前应可绕过 Windows 上 platform.machine 的 WMI 卡顿。"""
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    original = platform.machine

    patch_slow_platform_machine()

    assert platform.machine() == "AMD64"
    assert platform.machine is not original
