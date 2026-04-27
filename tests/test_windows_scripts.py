from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_superq_script_launches_goldminer_and_daemon() -> None:
    script = (ROOT / "start_superq.ps1").read_text(encoding="utf-8")

    assert "goldminer3.exe" in script
    assert "start.py" in script
    assert "--daemon" in script
    assert ".venv310" in script
    assert "superq_daemon.pid" in script


def test_stop_superq_script_stops_daemon_and_goldminer() -> None:
    script = (ROOT / "stop_superq.ps1").read_text(encoding="utf-8")

    assert "superq_daemon.pid" in script
    assert "Stop-Process" in script
    assert "goldminer3" in script


def test_double_click_bat_wrappers_call_powershell_scripts() -> None:
    start_bat = (ROOT / "start_superq.bat").read_text(encoding="utf-8")
    stop_bat = (ROOT / "stop_superq.bat").read_text(encoding="utf-8")

    assert "powershell" in start_bat.lower()
    assert "ExecutionPolicy Bypass" in start_bat
    assert "start \"\"" in start_bat.lower()
    assert "-WindowStyle Hidden" in start_bat
    assert "%~dp0start_superq.ps1" in start_bat
    assert "pause" not in start_bat.lower()
    assert "%~dp0stop_superq.ps1" in stop_bat
