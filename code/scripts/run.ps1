$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$projectPython = Join-Path $projectRoot 'deps\.venv\Scripts\python.exe'
& $projectPython (Join-Path $projectRoot 'code\main.py') @args
exit $LASTEXITCODE
