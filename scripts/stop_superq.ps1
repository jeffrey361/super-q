$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$DataDir = Join-Path $Root "data"
$PidFile = Join-Path $DataDir "superq_daemon.pid"

if (Test-Path $PidFile) {
    $PidText = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($PidText) {
        $Process = Get-Process -Id ([int]$PidText) -ErrorAction SilentlyContinue
        if ($Process) {
            Write-Host "Stopping superQ daemon: PID $PidText"
            Stop-Process -Id ([int]$PidText) -Force
        } else {
            Write-Host "superQ daemon PID not running: $PidText"
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "superQ daemon pid file not found"
}

$GoldminerProcesses = Get-Process -Name "goldminer3" -ErrorAction SilentlyContinue
if ($GoldminerProcesses) {
    Write-Host "Stopping Goldminer"
    $GoldminerProcesses | Stop-Process -Force
} else {
    Write-Host "Goldminer is not running"
}
