# coding=utf-8
"""一键启动 superQ。"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from super_q.core.config import Settings
from super_q.core.windows_compat import patch_slow_platform_machine

patch_slow_platform_machine()


def default_gm_python(root: Path) -> Path:
    if os.name == "nt":
        return root / ".venv310" / "Scripts" / "python.exe"
    return root / ".venv310" / "bin" / "python"


def build_once_commands(
    root: Path,
    current_python: str,
    gm_enabled: bool,
    gm_python: str = "",
) -> list[list[str]]:
    commands = [[current_python, str(root / "main.py")]]
    if not gm_enabled:
        return commands

    selected_gm_python = Path(gm_python) if gm_python else default_gm_python(root)
    if not selected_gm_python.exists():
        selected_gm_python = Path(current_python)
    commands.append([str(selected_gm_python), str(root / "gm_order_once.py")])
    return commands


def build_subprocess_env(source: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(source or os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        value = env.get(key, "")
        if value and urlparse(value).scheme not in {"http", "https"}:
            env.pop(key, None)
    return env


def run_once(root: Path, settings: Settings, gm_python: str = "") -> int:
    print("superQ 一键启动：准备执行主流程", flush=True)
    commands = build_once_commands(
        root=root,
        current_python=sys.executable,
        gm_enabled=settings.gm_enabled,
        gm_python=gm_python or os.getenv("GM_PYTHON", ""),
    )
    env = build_subprocess_env()
    for command in commands:
        print(f"RUN: {' '.join(command)}", flush=True)
        completed = subprocess.run(command, cwd=str(root), env=env, check=False)
        print(f"DONE: {' '.join(command)} -> {completed.returncode}", flush=True)
        if completed.returncode != 0:
            return completed.returncode
    return 0


def seconds_until_next_run(run_time: str, now: str | datetime | None = None) -> int:
    current = datetime.fromisoformat(now) if isinstance(now, str) else now or datetime.now()
    hour, minute, second = [int(part) for part in run_time.split(":")]
    target = current.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return int((target - current).total_seconds())


def main() -> None:
    parser = argparse.ArgumentParser(description="superQ 一键启动")
    parser.add_argument("--daemon", action="store_true", help="常驻模式，每天定时运行")
    parser.add_argument("--time", default=os.getenv("START_RUN_TIME", "14:45:00"), help="常驻模式每日运行时间")
    parser.add_argument("--gm-python", default=os.getenv("GM_PYTHON", ""), help="GM 下单脚本使用的 Python")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env", override=True)
    settings = Settings(_env_file=str(root / ".env"))

    if not args.daemon:
        raise SystemExit(run_once(root, settings, gm_python=args.gm_python))

    print(f"superQ 常驻启动，每天 {args.time} 运行。按 Ctrl+C 退出。", flush=True)
    while True:
        wait_seconds = seconds_until_next_run(args.time)
        next_time = datetime.now() + timedelta(seconds=wait_seconds)
        print(f"下一次运行：{next_time:%Y-%m-%d %H:%M:%S}", flush=True)
        time.sleep(wait_seconds)
        code = run_once(root, settings, gm_python=args.gm_python)
        print(f"本轮运行结束，退出码：{code}", flush=True)


if __name__ == "__main__":
    main()
