$ErrorActionPreference = 'Stop'
$taskProject = $PSScriptRoot
$taskPython = Join-Path $taskProject '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    python -m venv (Join-Path $taskProject '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
& $taskPython -m pip install -e "$taskProject[build]"
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies could not be installed.' }
& $taskPython -m PyInstaller --noconfirm --onefile --console --name disk-sweep --collect-data disk_sweep --collect-data textual --copy-metadata textual --distpath (Join-Path $taskProject 'dist') --workpath (Join-Path $taskProject 'artifacts/build') --specpath (Join-Path $taskProject 'artifacts') (Join-Path $taskProject 'src/disk_sweep/__main__.py')
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed.' }
Write-Host "Ready: $taskProject/dist/disk-sweep.exe"
