"""一键启动脚本测试。"""

from pathlib import Path

import start


def test_build_once_commands_uses_current_python_for_main_and_venv310_for_gm(
    tmp_path: Path,
) -> None:
    """一键启动应先跑 main.py，再用 .venv310 Python 跑 gm_order_once.py。"""
    root = tmp_path
    gm_python = root / ".venv310" / "Scripts" / "python.exe"
    gm_python.parent.mkdir(parents=True)
    gm_python.write_text("", encoding="utf-8")

    commands = start.build_once_commands(
        root=root,
        current_python="python-main",
        gm_enabled=True,
        gm_python="",
    )

    assert commands == [
        ["python-main", str(root / "main.py")],
        [str(gm_python), str(root / "gm_order_once.py")],
    ]


def test_build_once_commands_can_skip_gm(tmp_path: Path) -> None:
    """GM 未启用时只运行主流程。"""
    commands = start.build_once_commands(
        root=tmp_path,
        current_python="python-main",
        gm_enabled=False,
        gm_python="",
    )

    assert commands == [["python-main", str(tmp_path / "main.py")]]


def test_seconds_until_next_run_returns_next_day_when_time_passed() -> None:
    """当前时间已过目标时间时，应等待到次日目标时间。"""
    seconds = start.seconds_until_next_run("15:00:00", now="2026-04-25 16:00:00")

    assert seconds == 23 * 60 * 60


def test_build_subprocess_env_removes_malformed_proxy_values() -> None:
    """GM SDK 不能解析无协议头的 http_proxy，应从子进程环境里移除。"""
    env = start.build_subprocess_env({
        "http_proxy": "127.0.0.1:7897",
        "https_proxy": "http://127.0.0.1:7897",
        "NO_PROXY": "localhost,127.0.0.1",
    })

    assert "http_proxy" not in env
    assert env["https_proxy"] == "http://127.0.0.1:7897"
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
