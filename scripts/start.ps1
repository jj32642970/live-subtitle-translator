$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Config = Join-Path $Root "config\config.toml"
$LogDir = Join-Path $Root "logs"
$OutLog = Join-Path $LogDir "live.log"
$ErrLog = Join-Path $LogDir "live.err.log"
$ProjectPattern = [regex]::Escape($Root)

Set-Location $Root
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

if (-not (Test-Path $Python)) {
    Write-Host "Start failed: virtualenv Python was not found."
    Write-Host $Python
    exit 1
}

$existing = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
    Where-Object { $_.CommandLine -match $ProjectPattern -and $_.CommandLine -match "app\.py" }

if ($existing) {
    Write-Host "Already running. No need to start again."
    $existing | Select-Object ProcessId, CommandLine
    Write-Host ""
    Write-Host "Subtitle page: http://127.0.0.1:8080"
    exit 0
}

Remove-Item -LiteralPath $OutLog -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $ErrLog -Force -ErrorAction SilentlyContinue

$process = Start-Process `
    -FilePath $Python `
    -ArgumentList @("-u", "app.py", "--config", $Config) `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru

Start-Sleep -Seconds 5

if ($process.HasExited) {
    Write-Host "Start failed: service process exited."
    if (Test-Path $OutLog) {
        Write-Host ""
        Write-Host "Output log:"
        Get-Content $OutLog -Tail 40
    }
    if (Test-Path $ErrLog) {
        Write-Host ""
        Write-Host "Error log:"
        Get-Content $ErrLog -Tail 40
    }
    exit 1
}

Write-Host "Started successfully."
Write-Host "Process ID: $($process.Id)"
Write-Host "Subtitle page: http://127.0.0.1:8080"
Write-Host "OBS transparent page: http://127.0.0.1:8080?transparent=1"
Write-Host "Log file: $OutLog"
