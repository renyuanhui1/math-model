param(
    [string]$Python = "python",
    [string]$DataRoot = "",
    [string]$Template = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvRoot = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

if ($DataRoot) { $env:HUAWEI_D_DATA_ROOT = $DataRoot }
if ($Template) { $env:HUAWEI_D_TEMPLATE = $Template }

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv $venvRoot
}
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $projectRoot "requirements.txt")
& $venvPython (Join-Path $projectRoot "run_all.py")
