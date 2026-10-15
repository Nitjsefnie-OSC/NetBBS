#!/usr/bin/env python3
"""Plan the split of src/netbbs/net/admin_flow.py into admin_*.py modules (issue #1311).

`scripts/admin_split_map.json` assigns every top-level name in admin_flow.py
to its target module ("*" means every module defines its own, like
`_logger`). This script reads the current source and reports, for the next
move, what the map alone cannot show:

    python scripts/admin_split.py report            # every target, in move order
    python scripts/admin_split.py report admin_trust

For one target it lists:

- names still in admin_flow.py that the map does not assign (added since the
  map was made): assign them in the JSON before moving;
- names the target would still need from admin_flow.py: a move must come
  after the modules those live in, or admin_flow and the new module would
  import each other;
- every test and src reference to the names that move, by kind
  (`import`, `attr`, `patch`), so the move PR updates all of them;
- patches aimed at admin_flow for names it only *imports* that the target's
  code uses: such a patch has to follow the code to the target module.
  tests/test_admin_patch_targets.py catches the ones that stop reaching
  anything, but not a patch that still reaches admin_flow while the screen a
  test drives has moved.

The script only reads. It is temporary: it goes with the last move.
"""

from __future__ import annotations

import argparse
import functools
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NET = ROOT / "src" / "netbbs" / "net"
SOURCE = NET / "admin_flow.py"
MAP = Path(__file__).with_name("admin_split_map.json")
MODULE = "netbbs.net.admin_flow"


def load_map() -> tuple[list[str], dict[str, str]]:
    raw = json.loads(MAP.read_text(encoding="utf-8"))
    order = [module for module in raw if module != "*"]
    return order, {name: module for module, names in raw.items() for name in names}


def statement_names(node: ast.stmt) -> list[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [n.id for target in node.targets for n in ast.walk(target) if isinstance(n, ast.Name)]
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


class Source:
    """The top-level names of admin_flow.py and what each of them reads."""

    def __init__(self, path: Path) -> None:
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.defined: dict[str, ast.stmt] = {}
        self.imported: dict[str, str] = {}
        self.reads: dict[str, set[str]] = defaultdict(set)
        for node in self.tree.body:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.imported[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.imported[alias.asname or alias.name.split(".")[0]] = alias.name
            for name in statement_names(node):
                self.defined[name] = node
        for name, node in self.defined.items():
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load) and inner.id != name:
                    self.reads[name].add(inner.id)

    def span(self, name: str) -> int:
        node = self.defined[name]
        return node.end_lineno - node.lineno + 1


def references(paths: list[Path], moving: set[str]) -> list[tuple[str, int, str, str]]:
    """(file, line, kind, name) for every use of `moving` names through admin_flow."""
    return sorted(ref for path in paths for ref in _all_references(path) if ref[3] in moving)


@functools.cache
def _all_references(path: Path) -> frozenset[tuple[str, int, str, str]]:
    """Every name `path` uses through admin_flow, whatever the target."""
    found = []
    text = path.read_text(encoding="utf-8")
    if "admin_flow" in text:
        tree = ast.parse(text)
        aliases = set()
        dotted_import = False
        nodes = list(ast.walk(tree))
        for node in nodes:
            if isinstance(node, ast.ImportFrom) and node.module == "netbbs.net":
                aliases |= {a.asname or a.name for a in node.names if a.name == "admin_flow"}
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == MODULE:
                        if alias.asname:
                            aliases.add(alias.asname)
                        else:
                            dotted_import = True
        rel = path.relative_to(ROOT).as_posix()
        for node in nodes:
            if isinstance(node, ast.ImportFrom) and node.module == MODULE:
                found += [(rel, node.lineno, "import", a.name) for a in node.names]
            elif isinstance(node, ast.Attribute) and (
                (isinstance(node.value, ast.Name) and node.value.id in aliases)
                or (dotted_import and ast.unparse(node.value) == MODULE)
            ):
                found.append((rel, node.lineno, "attr", node.attr))
            elif isinstance(node, ast.Call) and len(node.args) >= 2:
                first, second = node.args[0], node.args[1]
                if isinstance(first, ast.Name) and first.id in aliases and isinstance(second, ast.Constant) \
                        and isinstance(second.value, str):
                    found.append((rel, node.lineno, "patch", second.value))
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(MODULE + "."):
                found.append((rel, node.lineno, "patch", node.value[len(MODULE) + 1:].split(".")[0]))
    return frozenset(found)


def report(target: str, source: Source, mapping: dict[str, str]) -> None:
    moving = {n for n in source.defined if mapping.get(n) == target}
    lines = sum(source.span(n) for n in moving)
    print(f"== {target}: {len(moving)} names, {lines:,} lines still in admin_flow.py")
    if not moving:
        return

    needs = defaultdict(set)
    for name in moving:
        for read in source.reads[name]:
            if read in source.defined and read not in moving and mapping.get(read) != "*":
                needs[mapping.get(read, "UNMAPPED")].add(read)
    stay = {m: sorted(n) for m, n in needs.items() if m != target}
    if stay:
        print("   still needs from admin_flow.py (move those first):")
        for module, names in sorted(stay.items()):
            print(f"     {module}: {', '.join(names)}")
    else:
        print("   needs nothing that stays in admin_flow.py: ready to move")

    tests = sorted((ROOT / "tests").rglob("*.py"))
    srcs = [p for p in sorted((ROOT / "src").rglob("*.py")) if p != SOURCE]
    for label, paths in (("tests", tests), ("src", srcs)):
        refs = references(paths, moving)
        if refs:
            print(f"   {label} references ({len(refs)}):")
            for rel, line, kind, name in refs:
                print(f"     {rel}:{line}: {kind} {name}")

    used_imports = {r for n in moving for r in source.reads[n] if r in source.imported}
    patched = references(tests, used_imports)
    patched = [r for r in patched if r[2] in ("patch", "attr")]
    if patched:
        print("   imported names the target uses that tests patch or read on admin_flow:")
        for rel, line, kind, name in patched:
            others = sorted({mapping.get(n, "UNMAPPED") for n in source.defined
                             if name in source.reads[n] and n not in moving})
            also = f" (also used by {', '.join(others)})" if others else ""
            print(f"     {rel}:{line}: {kind} {name}{also}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report", help="what the next move involves")
    rep.add_argument("target", nargs="?", help="one target module, e.g. admin_trust")
    args = parser.parse_args(argv)

    order, mapping = load_map()
    source = Source(SOURCE)
    unmapped = sorted(n for n in source.defined if n not in mapping)
    if unmapped:
        print("UNMAPPED (assign in scripts/admin_split_map.json):", ", ".join(unmapped))
    targets = [args.target] if args.target else [m for m in order if m != "admin_flow"]
    for target in targets:
        if target not in order:
            parser.error(f"unknown target {target}; known: {', '.join(order)}")
        report(target, source, mapping)
    return 1 if unmapped else 0


if __name__ == "__main__":
    sys.exit(main())
