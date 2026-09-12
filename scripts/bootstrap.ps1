param([string]$Environment = ".venv-m1")
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "checked-native.ps1")
$root = Split-Path -Parent $PSScriptRoot
$python = Get-Command python -ErrorAction Stop
Invoke-CheckedNative "Python version verification" $python.Source @("-c", "import sys; assert sys.version_info[:3] == (3, 12, 10), sys.version")
if (Test-Path -LiteralPath (Join-Path $root $Environment)) { throw "Environment already exists: $Environment" }
Invoke-CheckedNative "virtual-environment creation" $python.Source @("-m", "venv", (Join-Path $root $Environment))
$venvPython = Join-Path $root "$Environment\Scripts\python.exe"
Invoke-CheckedNative "hash-locked dependency installation" $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--index-url", "https://pypi.org/simple", "--require-hashes", "-r", (Join-Path $root "requirements-dev.lock"))
Invoke-CheckedNative "project installation" $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--no-build-isolation", "--no-deps", $root)
Invoke-CheckedNative "M1 engineering-foundation checks" $venvPython @((Join-Path $root "scripts\m1_engineering_foundation.py"), "check")
