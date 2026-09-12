"""Enforce the strategy import boundary from Implementation Specification v1.0.

Section 4 prohibits strategy calls to broker/OMS; section 11 requires a negative
architecture test. With no submission submodules yet, both package namespaces
are prohibited, including future descendants. This rejects statically resolvable
references, including common dynamic-import calls. Arbitrary runtime-computed
names, reflection and transitive dependencies remain outside static AST proof.
"""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest


PROHIBITED = ("automated_trading_bot.broker", "automated_trading_bot.oms")


def _constant_string(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string(node.left)
        right = _constant_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _prohibited(target: str) -> bool:
    return any(target == prefix or target.startswith(prefix + ".") for prefix in PROHIBITED)


def _literal_strings(node: ast.expr) -> tuple[str, ...] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values = tuple(_constant_string(item) for item in node.elts)
    if any(value is None for value in values):
        return None
    return tuple(value for value in values if value is not None)


def assert_strategy_import_boundary(source_root: Path) -> None:
    strategy = source_root / "automated_trading_bot" / "strategy"
    assert strategy.is_dir(), "strategy package missing; cannot verify boundary"
    paths = sorted(strategy.rglob("*.py"))
    assert paths, "no strategy source files; cannot verify boundary"
    violations: list[str] = []
    for path in paths:
        package = ".".join(path.relative_to(source_root).parent.parts)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        importlib_aliases: set[str] = set()
        builtins_aliases: set[str] = set()
        import_module_aliases: set[str] = set()
        builtin_import_aliases: set[str] = {"__import__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "importlib":
                        importlib_aliases.add(alias.asname or alias.name)
                    elif alias.name == "builtins":
                        builtins_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module == "importlib":
                    import_module_aliases.update(
                        alias.asname or alias.name for alias in node.names
                        if alias.name == "import_module"
                    )
                elif node.module == "builtins":
                    builtin_import_aliases.update(
                        alias.asname or alias.name for alias in node.names
                        if alias.name == "__import__"
                    )
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
                if _prohibited(target):
                    violations.append(f"{path}:{node.lineno}: prohibited import {target}")
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function = node.func
            dynamic_kind: str | None = None
            if isinstance(function, ast.Name) and function.id in builtin_import_aliases:
                dynamic_kind = "__import__"
            elif isinstance(function, ast.Name) and function.id in import_module_aliases:
                dynamic_kind = "importlib.import_module"
            elif isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
                if function.attr == "__import__" and function.value.id in builtins_aliases:
                    dynamic_kind = "builtins.__import__"
                elif function.attr == "import_module" and function.value.id in importlib_aliases:
                    dynamic_kind = "importlib.import_module"
            target = _constant_string(node.args[0])
            if dynamic_kind == "importlib.import_module" and target and target.startswith("."):
                package_node = node.args[1] if len(node.args) > 1 else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "package"),
                    None,
                )
                package = _constant_string(package_node) if package_node is not None else None
                if package is not None:
                    try:
                        target = resolve_name(target, package)
                    except ImportError:
                        target = None
            dynamic_targets = [target] if target is not None else []
            if dynamic_kind in {"__import__", "builtins.__import__"} and target is not None:
                fromlist_node = node.args[3] if len(node.args) > 3 else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "fromlist"),
                    None,
                )
                fromlist = _literal_strings(fromlist_node) if fromlist_node is not None else None
                if fromlist is not None:
                    dynamic_targets.extend(f"{target}.{item}" for item in fromlist if item != "*")
            prohibited_target = next((item for item in dynamic_targets if _prohibited(item)), None)
            if dynamic_kind is not None and prohibited_target is not None:
                violations.append(
                    f"{path}:{node.lineno}: prohibited dynamic import "
                    f"{prohibited_target} via {dynamic_kind}"
                )
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
    "__import__('automated_trading_bot.broker')",
    "__import__('automated_trading_bot.oms.submission')",
    "import builtins\nbuiltins.__import__('automated_trading_bot.broker')",
    "import builtins as bi\nbi.__import__('automated_trading_bot.oms.submission')",
    "import importlib\nimportlib.import_module('automated_trading_bot.broker')",
    "import importlib as il\nil.import_module('automated_trading_bot.oms.submission')",
    "from importlib import import_module\nimport_module('automated_trading_bot.broker')",
    "from importlib import import_module as load\nload('automated_trading_bot.oms.submission')",
    "__import__('automated_trading_bot.' + 'broker')",
    "__import__('automated_trading_bot', fromlist=['broker'])",
    "import importlib\nimportlib.import_module('.broker', 'automated_trading_bot')",
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


def test_runtime_computed_dynamic_name_is_outside_static_proof(tmp_path: Path) -> None:
    strategy = tmp_path / "automated_trading_bot" / "strategy"
    strategy.mkdir(parents=True)
    (strategy / "proposal.py").write_text(
        "import importlib\n"
        "module_name = obtain_module_name()\n"
        "module = importlib.import_module(module_name)\n",
        encoding="utf-8",
    )
    assert_strategy_import_boundary(tmp_path)


def test_missing_strategy_package_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="strategy package missing"):
        assert_strategy_import_boundary(tmp_path)
