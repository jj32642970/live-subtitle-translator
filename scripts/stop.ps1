$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$ProjectPattern = [regex]::Escape($Root)

$processes = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
    Where-Object { $_.CommandLine -match $ProjectPattern -and $_.CommandLine -match "app\.py" }

if (-not $processes) {
    Write-Host "Service is not running."
    exit 0
}

foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped process: $($process.ProcessId)"
}

Start-Sleep -Seconds 1

$listeners = Get-NetTCPConnection -LocalPort 8080,8765 -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
    Write-Host "Warning: ports 8080 or 8765 are still in use:"
    $listeners | Select-Object LocalAddress, LocalPort, State, OwningProcess
} else {
    Write-Host "Service stopped. Ports 8080 and 8765 are free."
}
