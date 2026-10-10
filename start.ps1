param([switch]$Demo)
$ErrorActionPreference = 'Stop'
$taskProject = $PSScriptRoot
$taskPython = Join-Path $taskProject '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -m venv (Join-Path $taskProject '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
& $taskPython -c 'import disk_sweep, textual, send2trash' 2>$null
if ($LASTEXITCODE -ne 0) {
    & $taskPython -m pip install -e $taskProject
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if ($Demo) { & $taskPython -m disk_sweep --demo }
else { & $taskPython -m disk_sweep }
exit $LASTEXITCODE
