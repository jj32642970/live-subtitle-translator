$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$OutLog = Join-Path $Root "logs\live.log"
$ErrLog = Join-Path $Root "logs\live.err.log"
$ProjectPattern = [regex]::Escape($Root)

$processes = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
    Where-Object { $_.CommandLine -match $ProjectPattern -and $_.CommandLine -match "app\.py" }

if ($processes) {
    Write-Host "Service is running:"
    $processes | Select-Object ProcessId, CommandLine
} else {
    Write-Host "Service is not running."
}

Write-Host ""
Write-Host "Listening ports:"
$listeners = Get-NetTCPConnection -LocalPort 8080,8765 -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
    $listeners | Select-Object LocalAddress, LocalPort, State, OwningProcess
} else {
    Write-Host "Ports 8080 and 8765 are free."
}

if (Test-Path $OutLog) {
    Write-Host ""
    Write-Host "Recent output log:"
    Get-Content $OutLog -Tail 20
}

if (Test-Path $ErrLog) {
    $err = Get-Content $ErrLog -Tail 20
    if ($err) {
        Write-Host ""
        Write-Host "Recent error log:"
        $err
    }
}
