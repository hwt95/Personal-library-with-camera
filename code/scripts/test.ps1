$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$projectPython = Join-Path $projectRoot 'deps\.venv\Scripts\python.exe'
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot 'test\data') | Out-Null
Push-Location $projectRoot
try {
    & $projectPython -m pytest test -q --basetemp 'test\data\pytest-current' --junitxml 'test\reports\tests.xml'
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $projectPython 'code\main.py' --self-test 'test\reports\source-smoke.json'
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
