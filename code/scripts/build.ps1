$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$projectPython = Join-Path $projectRoot 'deps\.venv\Scripts\python.exe'
& $projectPython (Join-Path $PSScriptRoot 'build.py')
exit $LASTEXITCODE
