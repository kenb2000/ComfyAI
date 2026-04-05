[CmdletBinding()]
param(
    [switch]$NoLaunchComfyUI,
    [switch]$NoLaunchPlanner,
    [double]$StartTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$PythonCommand = $null
$PythonArgs = @()
$RepoPython = Join-Path $RepoRoot "tools\comfyhybrid-venv\Scripts\python.exe"
$LocalPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (Test-Path $RepoPython) {
    $PythonCommand = $RepoPython
}
elseif (Test-Path $LocalPython) {
    $PythonCommand = $LocalPython
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCommand = "python"
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCommand = "py"
    $PythonArgs += "-3"
}
else {
    Write-Host "FAIL: Python was not found. Install Python 3.10+ and rerun from the repo root."
    exit 1
}

$ScriptArgs = @(
    (Join-Path $RepoRoot "scripts\comfyhybrid_setup_flow.py"),
    "verify"
)
if ($NoLaunchComfyUI) { $ScriptArgs += "--no-launch-comfyui" }
if ($NoLaunchPlanner) { $ScriptArgs += "--no-launch-planner" }
$ScriptArgs += "--start-timeout-seconds"
$ScriptArgs += "$StartTimeoutSeconds"

Write-Host "Running ComfyUIhybrid verify from $RepoRoot"
Write-Host "Using Python: $PythonCommand"

& $PythonCommand @PythonArgs @ScriptArgs
exit $LASTEXITCODE
