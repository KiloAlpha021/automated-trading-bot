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

The local checkpoint uses CPython 3.14.7 on Windows. Supported Python versions
and application dependencies remain pending baseline review. Version `0.0.0`
is placeholder package metadata, not an application release.

To create an isolated environment and install the scaffold in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -c "import automated_trading_bot; print(automated_trading_bot.__file__)"
```

Installation needs access to the setuptools build dependency. There are no
runtime dependencies, entry points, or substantive tests at this stage.
