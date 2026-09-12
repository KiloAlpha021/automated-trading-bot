"""Enforce the strategy import boundary from Implementation Specification v1.0.

Section 4 prohibits strategy calls to broker/OMS; section 11 requires a negative
architecture test. With no submission submodules yet, both package namespaces
are prohibited, including future descendants. This is a static import-statement
check, not a sandbox for dynamic imports, reflection or transitive dependencies.
"""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest


PROHIBITED = ("automated_trading_bot.broker", "automated_trading_bot.oms")


def assert_strategy_import_boundary(source_root: Path) -> None:
    strategy = source_root / "automated_trading_bot" / "strategy"
    assert strategy.is_dir(), "strategy package missing; cannot verify boundary"
    paths = sorted(strategy.rglob("*.py"))
    assert paths, "no strategy source files; cannot verify boundary"
    violations: list[str] = []
    for path in paths:
        package = ".".join(path.relative_to(source_root).parent.parts)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                name = "." * node.level + (node.module or "")
                base = resolve_name(name, package) if node.level else name
                targets = [base] + [f"{base}.{alias.name}" for alias in node.names]
                # A root wildcard cannot prove that broker/OMS is excluded.
                if base == "automated_trading_bot" and any(a.name == "*" for a in node.names):
                    violations.append(f"{path}:{node.lineno}: root wildcard import")
            for target in targets:
                if any(target == prefix or target.startswith(prefix + ".") for prefix in PROHIBITED):
                    violations.append(f"{path}:{node.lineno}: prohibited import {target}")
    assert not violations, "Strategy import boundary violated:\n" + "\n".join(violations)


def test_current_strategy_import_boundary() -> None:
    assert_strategy_import_boundary(Path(__file__).resolve().parents[1] / "src")


@pytest.mark.parametrize("statement", [
    "import automated_trading_bot.broker",
    "import automated_trading_bot.oms",
    "import automated_trading_bot.broker.adapter as adapter",
    "from automated_trading_bot.oms.submission import submit",
    "from automated_trading_bot import broker as adapter",
    "from automated_trading_bot import oms",
    "from ...broker import submit",
    "from ... import oms",
    "from automated_trading_bot.broker import *",
    "from automated_trading_bot import *",
    "def propose():\n    import automated_trading_bot.oms",
    "if False:\n    from automated_trading_bot.broker import submit",
])
@pytest.mark.parametrize("filename", ["proposal.py", "__init__.py"])
def test_deliberate_prohibited_strategy_import_fails(
    tmp_path: Path, statement: str, filename: str,
) -> None:
    # Run the same repository checker over real fixture files. No broker or OMS
    # implementation is needed, imported or executed; fixture code is parsed only.
    nested = tmp_path / "automated_trading_bot" / "strategy" / "nested"
    nested.mkdir(parents=True)
    path = nested / filename
    path.write_text(statement + "\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="Strategy import boundary violated") as error:
        assert_strategy_import_boundary(tmp_path)
    assert str(path) in str(error.value)


def test_allowed_strategy_imports_pass(tmp_path: Path) -> None:
    strategy = tmp_path / "automated_trading_bot" / "strategy"
    strategy.mkdir(parents=True)
    (strategy / "proposal.py").write_text(
        "from automated_trading_bot.domain.decision import Proposal\n"
        "from . import helper\n"
        "import decimal\n"
        "# import automated_trading_bot.broker\n"
        "description = 'from automated_trading_bot.oms import submit'\n",
        encoding="utf-8",
    )
    assert_strategy_import_boundary(tmp_path)


def test_missing_strategy_package_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="strategy package missing"):
        assert_strategy_import_boundary(tmp_path)
