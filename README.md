# Automated Trading Bot

ATIS contains a Python implementation, tests, engineering scripts and project
documentation. Source presence and passing component tests do not establish
operational qualification. ATIS is not represented here as operationally
complete, Stage 5 complete, paper-trading ready or authorised, or live-trading
authorised. Financial performance is not established, and implemented modules
must not be assumed independently qualified for operational use.

## Layout

- `src/automated_trading_bot/`: setuptools-discovered Python package, including
  domain, accounting, market data, instruments, calendars, corporate actions,
  datasets, strategy, research, backtesting, economic edge, portfolio, risk,
  execution, OMS, broker, reconciliation, recovery, audit and monitoring areas.
  Package areas have differing implementation and qualification status.
- `tests/`: existing component, contract, regression and engineering-control tests.
- `scripts/`: bootstrap, checked native execution, M1 validation, Stage 0B
  validation, specification extraction and development-lock generation scripts.
- `docs/`: baseline specifications, M1 evidence, stage documentation and programme
  records. Historical evidence remains evidence of its stated subject and scope.
- `config/`: reserved for configuration; `config/.gitkeep` retains this directory.

The Technical Design, Implementation Specification, Stage 0B baseline and
canonical invariants remain authoritative within their accepted scopes; see
`docs/baseline/` and the applicable accepted contracts and amendments.

## Governance and qualification

The owner identifies [Smoothflow V5.5 engineering governance](docs/programme/ATIS-SMOOTHFLOW-V5.5-ENGINEERING-GOVERNANCE.md)
as accepted for this bounded housekeeping task, bound to protected Git-byte
SHA256 `e96296c6e1a2fb5c1bf7e9e5f9b24f57312fe1c7298187a61d13f3aed879512c`.
The document retains its original preparation and effectiveness wording; this
README does not rewrite that history or establish technical enforcement.

Implemented code, tested components, independent assurance, operational
qualification, owner acceptance and trading authority are separate states.
Existing M1 validation covers engineering controls; protected CI and trusted
evaluator validation remain distinct certification obligations. Neither a local
test run nor this README grants paper-trading or live-trading authority.

## Bootstrap environment

Run from the repository root with CPython 3.12.10 available as `python`.
The existing bootstrap entry point is:

```powershell
.\scripts\bootstrap.ps1
```

The full command requires the protected hosted execution context supplied by
`.github/workflows/m1-engineering-foundation.yml`: the runner checks GitHub
attribution, the sealed candidate and external evidence/state directories.
A standalone shell without that context fails closed at validation. Do not
fabricate hosted variables to bypass this requirement. For separately authorised
local validation, the existing runner accepts `preflight --contract <path>` and
`check --contract <path>` with a valid execution contract; see
`docs/m1-closure/engineering-foundation-execution-contract.schema.json` and
`docs/m1-closure/M1-engineering-foundation-scope-clarification.md`.

The command creates a new `.venv-m1`, installs the exact hash-locked dependency
set from public PyPI, verifies package identity and importability, then runs Ruff,
strict mypy, pytest under coverage, pip consistency and vulnerability checks,
and the repository's bounded secret scan. Delete the generated environment before
running the command again; refusing an existing environment prevents accidental
dependence on prior state.

The reproducibility checkpoint uses CPython 3.12.10 on Windows. `.python-version`
records that exact interpreter; it does not install Python. No broader Python
compatibility claim is made. There are no runtime dependencies.

`requirements-dev.lock` pins pip, the declared setuptools build backend, pytest,
and its resolved dependencies, with SHA-256 hashes of their PyPI wheels. The current
build-backend declaration in `pyproject.toml`, development input and lock is
`setuptools==84.1.0`. The lock covers the Windows x86-64 development environment.

For local installation and direct tests, create a new environment with CPython
3.12.10 and install in PowerShell from the repository root. These commands do
not replace contract-based M1 evidence, protected CI or trusted evaluation:

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
set matches the canonical declarations in `pyproject.toml` and
`requirements-dev.in`. When intentionally
changing dependencies, resolve those declarations again, retain unaffected pins,
and regenerate hashes from the selected distribution files.
