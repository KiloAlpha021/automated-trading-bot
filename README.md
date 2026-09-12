# Automated Trading Bot

Repository bootstrap only. No trading implementation is present.

The audited Technical Design, Implementation Specification, Stage 0B baseline,
and canonical invariants remain authoritative. Those documents are not yet
present in this repository; consult them before substantive implementation.
This scaffold does not replace or revise that baseline.

## Layout

- `src/automated_trading_bot/`: existing Python package scaffold.
- `tests/`: reserved for tests.
- `config/`: reserved for configuration.
- `docs/`: reserved for project documentation.
- `scripts/`: reserved for development scripts.

Empty directories contain `.gitkeep` placeholders so Git can track them.

## Bootstrap environment

The reproducibility checkpoint uses CPython 3.12.10 on Windows. `.python-version`
records that exact interpreter; it does not install Python. No broader Python
compatibility claim is made. There are no runtime dependencies.

`requirements-dev.lock` pins pip, the declared setuptools build backend, pytest,
and its resolved dependencies, with SHA-256 hashes of their PyPI wheels. Existing
test dependency versions are retained. Setuptools was previously unpinned and
absent from the project environment; its existing build requirement resolved to
84.0.0. The lock covers the Windows development environment.

Create a new environment with CPython 3.12.10 and install in PowerShell:

```powershell
py -3.12 -c "import sys; assert sys.version_info[:3] == (3, 12, 10)"
py -3.12 -m venv .venv-reproduction
.\.venv-reproduction\Scripts\python.exe -m pip install --index-url https://pypi.org/simple --require-hashes -r requirements-dev.lock
.\.venv-reproduction\Scripts\python.exe -m pip install --no-build-isolation --no-deps .
.\.venv-reproduction\Scripts\python.exe -m pip check
.\.venv-reproduction\Scripts\python.exe -c "import automated_trading_bot; print(automated_trading_bot.__file__)"
.\.venv-reproduction\Scripts\python.exe -m pytest -q
```

Use the locked backend via `--no-build-isolation`; `--no-deps` prevents the
project installation from resolving an alternative dependency set. To verify
installed versions against the lock (excluding the local project):

```powershell
.\.venv-reproduction\Scripts\python.exe -c "import importlib.metadata as m; from pathlib import Path; expected=dict(line.split()[0].split('==') for line in Path('requirements-dev.lock').read_text().splitlines() if line and not line.startswith('#')); actual={d.metadata['Name'].lower().replace('_','-'):d.version for d in m.distributions() if d.metadata['Name'].lower().replace('_','-') != 'automated-trading-bot'}; assert actual == expected, (actual, expected); print('Exact locked versions verified')"
```

The lock was generated from the resolved wheel metadata and wheel SHA-256
hashes for `.[dev]`, the declared build backend, and pip, retaining the installed
test versions. An offline pip resolution against those wheels verifies that this
set matches the canonical declarations in `pyproject.toml`. When intentionally
changing dependencies, resolve those declarations again, retain unaffected pins,
and regenerate hashes from the selected distribution files.
