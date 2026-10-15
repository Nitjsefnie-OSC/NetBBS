"""A patch aimed at a SysOp console module must reach code in it (issue #1311).

`monkeypatch.setattr(admin_flow, "_x", fake)` replaces the attribute `_x` of
the module object. Only code that looks `_x` up in that module's globals sees
the fake. When the function that calls `_x` moves to another `admin_*` module
(issue #1311 splits admin_flow.py), a patch still aimed at `admin_flow` keeps
succeeding and stops reaching anything: the test goes on passing and no longer
tests what it says. This guard fails instead. Every name a test patches on an
`admin*` module must be read by code defined in that module.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_NET = _ROOT / "src" / "netbbs" / "net"
_PACKAGE = "netbbs.net."


def _admin_modules() -> dict[str, Path]:
    return {f"{_PACKAGE}{path.stem}": path for path in sorted(_NET.glob("admin*.py"))}


def _names_read(path: Path) -> set[str]:
    """Every global name read by code in the module, outside its imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    read = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            read.add(node.id)
    return read


def _module_aliases(tree: ast.Module, modules: dict[str, Path]) -> dict[str, str]:
    """Local names a test file binds to an admin module, mapped to its dotted name."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _PACKAGE.rstrip("."):
            for alias in node.names:
                dotted = f"{_PACKAGE}{alias.name}"
                if dotted in modules:
                    aliases[alias.asname or alias.name] = dotted
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in modules:
                    aliases[alias.asname or alias.name] = alias.name
    return aliases


def _patch_targets(tree: ast.Module, modules: dict[str, Path]) -> list[tuple[int, str, str]]:
    """(line, module, name) for every setattr/patch/patch.object aimed at an admin module."""
    aliases = _module_aliases(tree, modules)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def looped(name: ast.Name, call: ast.Call) -> list[str] | None:
        """The literals of the nearest enclosing `for <name> in ("_a", "_b"):`."""
        node = parents.get(call)
        while node is not None:
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == name.id:
                if isinstance(node.iter, (ast.Tuple, ast.List)) and all(
                        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.iter.elts):
                    return [e.value for e in node.iter.elts]
                return None
            node = parents.get(node)
        return None

    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            # a hand-rolled patch: `admin_flow._x = fake`
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                        and target.value.id in aliases:
                    targets.append((node.lineno, aliases[target.value.id], target.attr))
            continue
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = ast.unparse(node.func)
        first = node.args[0]
        if function.endswith(("setattr", "patch.object")) and len(node.args) >= 2:
            second = node.args[1]
            dotted = aliases.get(first.id) if isinstance(first, ast.Name) else None
            if dotted is None and isinstance(first, ast.Attribute) and ast.unparse(first) in modules:
                dotted = ast.unparse(first)
            if dotted is not None and isinstance(second, ast.Constant) and isinstance(second.value, str):
                targets.append((node.lineno, dotted, second.value))
                continue
            names = looped(second, node) if dotted is not None and isinstance(second, ast.Name) else None
            if names is not None:
                targets += [(node.lineno, dotted, name) for name in names]
                continue
        if (function.endswith("setattr") or function == "patch" or function.endswith(".patch")) and \
                isinstance(first, ast.Constant) and isinstance(first.value, str):
            for dotted in modules:
                if first.value.startswith(dotted + "."):
                    targets.append((node.lineno, dotted, first.value[len(dotted) + 1:].split(".")[0]))
    return targets


def test_every_patch_on_an_admin_module_reaches_code_in_it():
    modules = _admin_modules()
    read = {dotted: _names_read(path) for dotted, path in modules.items()}
    stale = []
    for path in sorted((_ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, dotted, name in _patch_targets(tree, modules):
            if name not in read[dotted]:
                stale.append(f"{path.relative_to(_ROOT).as_posix()}:{line}: {dotted}.{name}")
    assert not stale, (
        "these patches replace a name no code in the module reads, so they reach nothing; "
        "aim them at the module whose code uses the name:\n" + "\n".join(stale)
    )


def test_the_guard_sees_every_patch_form(tmp_path):
    source = """
import netbbs.net.admin_flow
from netbbs.net import admin_flow
from netbbs.net import admin_flow as af
from unittest.mock import patch

def test(monkeypatch):
    monkeypatch.setattr(admin_flow, "one", None)
    monkeypatch.setattr(af, "two", None)
    monkeypatch.setattr(netbbs.net.admin_flow, "three", None)
    monkeypatch.setattr("netbbs.net.admin_flow.four", None)
    with patch("netbbs.net.admin_flow.five"), patch.object(af, "six"):
        pass
    admin_flow.seven = None
    for name in ("eight", "nine"):
        monkeypatch.setattr(admin_flow, name, None)
    for name in ("not", "patched"):
        pass
"""
    targets = _patch_targets(ast.parse(source), {"netbbs.net.admin_flow": tmp_path})
    assert sorted(name for _, _, name in targets) == [
        "eight", "five", "four", "nine", "one", "seven", "six", "three", "two",
    ]
