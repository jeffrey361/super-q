$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $Root "data"
$PidFile = Join-Path $DataDir "superq_daemon.pid"
$OutLog = Join-Path $DataDir "superq_daemon.out.log"
$ErrLog = Join-Path $DataDir "superq_daemon.err.log"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$GmPython = Join-Path $Root ".venv310\Scripts\python.exe"
$StartPy = Join-Path $Root "start.py"
$Goldminer = "C:\Users\shao\AppData\Roaming\Hongshu Goldminer3\goldminer3.exe"
$RunTime = if ($env:START_RUN_TIME) { $env:START_RUN_TIME } else { "14:45:00" }

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Set-Location $Root

if (Test-Path $PidFile) {
    $ExistingPid = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($ExistingPid) {
        $ExistingProcess = Get-Process -Id ([int]$ExistingPid) -ErrorAction SilentlyContinue
        if ($ExistingProcess) {
            Write-Host "superQ daemon already running: PID $ExistingPid"
            exit 0
        }
    }
}

if (-not (Get-Process -Name "goldminer3" -ErrorAction SilentlyContinue)) {
    if (-not (Test-Path $Goldminer)) {
        throw "Goldminer not found: $Goldminer"
    }
    Write-Host "Starting Goldminer: $Goldminer"
    Start-Process -FilePath $Goldminer -WorkingDirectory (Split-Path -Parent $Goldminer)
    Start-Sleep -Seconds 20
} else {
    Write-Host "Goldminer already running"
}

if (-not (Test-Path $Python)) {
    throw "Python not found: $Python"
}

$Arguments = @(
    "`"$StartPy`"",
    "--daemon",
    "--time", $RunTime,
    "--gm-python", "`"$GmPython`""
) -join " "

Write-Host "Starting superQ daemon at $RunTime"
$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList $Arguments `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden `
    -PassThru

Set-Content -Path $PidFile -Value $Process.Id -Encoding UTF8
Write-Host "superQ daemon started: PID $($Process.Id)"
Write-Host "stdout: $OutLog"
Write-Host "stderr: $ErrLog"
