param([string]$Environment = ".venv-m1")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Get-Command python -ErrorAction Stop
& $python.Source -c "import sys; assert sys.version_info[:3] == (3, 12, 10), sys.version"
if (Test-Path -LiteralPath (Join-Path $root $Environment)) { throw "Environment already exists: $Environment" }
& $python.Source -m venv (Join-Path $root $Environment)
$venvPython = Join-Path $root "$Environment\Scripts\python.exe"
& $venvPython -m pip install --disable-pip-version-check --no-cache-dir --index-url https://pypi.org/simple --require-hashes -r (Join-Path $root "requirements-dev.lock")
& $venvPython -m pip install --disable-pip-version-check --no-build-isolation --no-deps $root
& $venvPython (Join-Path $root "scripts\m1_engineering_foundation.py") check
