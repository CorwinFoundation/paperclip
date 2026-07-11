$ErrorActionPreference = "Stop"

$bundledPython = "C:\Users\katza\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$python = if (Test-Path -LiteralPath $bundledPython) { $bundledPython } else { "python" }
$runsDir = Join-Path $env:TEMP "backbond-passive-showcase-runs"

Write-Host "BackBond live proof environment"
Write-Host "Open http://127.0.0.1:8765"
Write-Host "Press Ctrl+C to stop."

& $python "$PSScriptRoot\app.py" --host 127.0.0.1 --port 8765 --runs-dir $runsDir
