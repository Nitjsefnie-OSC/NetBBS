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

`move TARGET` then does the move itself:

    python scripts/admin_split.py move admin_trust

It cuts every statement the map assigns to TARGET out of admin_flow.py, with
the comment lines directly above each one. It writes them in their original
order to src/netbbs/net/TARGET.py, which imports what they read from
admin_flow.py's own imports. It prunes the imports admin_flow.py no longer
uses and imports back the moved names that its remaining code calls. Then it
points every src and test import, attribute read and patch of a moved name
at TARGET. A patch stays on admin_flow when only admin_flow's code calls the
name, because admin_flow looks the name up in its own globals. Anything it
cannot decide (CHECK) or cannot resolve (WARNING) is printed for a human.
Code is moved byte for byte; `git diff --color-moved` shows it as moved.

The script is temporary: it goes with the last move.
"""

from __future__ import annotations

import argparse
import ast
import functools
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


# -- move ---------------------------------------------------------------------

DOCSTRINGS = {
    "admin_ui": """Shared screen machinery of the SysOp console.

Announcements, choice prompts, pickers, draft editors, detail views, the
panel writers and the upload helpers that every `admin_*` module builds its
screens from. Split out of `netbbs.net.admin_flow` (issue #1311).""",
    "admin_fields": """Draft-editor fields shared across the SysOp console.

Integer, minimum-age, name-requirement and level fields, the seeded line
reader and the redraw-preference lookup that the board, file-area, channel,
door, trust and settings editors all use. Split out of
`netbbs.net.admin_flow` (issue #1311).""",
    "admin_resources": """Listing and detail helpers shared by the SysOp resource screens.

Boards, file areas, chat channels and Communities list and describe
themselves the same way: effective access, the level and gate columns, Link
reach, carried-copy hiding and removal. Split out of `netbbs.net.admin_flow`
(issue #1311).""",
    "admin_maintenance": """SysOp console maintenance screens.

Release check and install, backups and their schedule, the diagnostic,
audit and node logs, carried-post repair, draft pruning and search-index
rebuilds. Split out of `netbbs.net.admin_flow` (issue #1311).""",
    "admin_trust": """SysOp console trust screens.

Vouches, trust subjects and overrides, trust domains, anchors and
reporters, attestation authorities and recipients, the published identity,
exceptions and retired usernames. Split out of `netbbs.net.admin_flow`
(issue #1311).""",
    "admin_link": """SysOp console Link screens.

Link status, the node map, carry decisions, the published dial-in, node
keys, the Outbox and the mail tools. Split out of `netbbs.net.admin_flow`
(issue #1311).""",
    "admin_banners": """SysOp console banner, masthead and theme-colour screens.

The welcome, main-menu, logoff and new-account banners, the board-list,
file-area and chat-channel-picker mastheads, and the theme colours. Split
out of `netbbs.net.admin_flow` (issue #1311).""",
    "admin_moderation": """SysOp console moderation queue.

Held posts and pending files, reviewed from the staff menu, the board and
file-area screens and the caller-side queue. Split out of
`netbbs.net.admin_flow` (issue #1311).""",
    "admin_system": """SysOp console System menu.

Node name, Link participation, guest access, timestamps, limits, policy,
MRC settings and status, and the Managed DNS status. Split out of
`netbbs.net.admin_flow` (issue #1311).""",
    "admin_sessions": """SysOp console Node menu.

Who's online, disconnecting a session, scheduled shutdown, maintenance
mode, lock and drain, and revoking live sessions after an account change.
Split out of `netbbs.net.admin_flow` (issue #1311).""",
    "admin_boards": """SysOp console board, Community, category and moderator screens.

Split out of `netbbs.net.admin_flow` (issue #1311).""",
    "admin_files": """SysOp console file-area screens.

File areas, their garbage collection and expired files. Split out of
`netbbs.net.admin_flow` (issue #1311).""",
    "admin_chat": """SysOp console chat-channel screens.

Chat channels, MRC rooms (adopt, retire, bridge, pause) and channel
restrictions. Split out of `netbbs.net.admin_flow` (issue #1311).""",
    "admin_doors": """SysOp console door screens.

Doors, their gallery and outbound targets, door-file uploads and the War
Dialer world administration. Split out of `netbbs.net.admin_flow`
(issue #1311).""",
    "admin_users": """SysOp console user screens.

The users menu, levels, gates and promotion rules, creating, listing and
editing users, staff permissions and registration settings. Split out of
`netbbs.net.admin_flow` (issue #1311).""",
}

LINE_LIMIT = 99


def format_from(module: str, parts: list[str], indent: str = "") -> str:
    one = f"{indent}from {module} import {', '.join(parts)}"
    if len(one) <= LINE_LIMIT:
        return one
    return f"{indent}from {module} import (\n" + "".join(f"{indent}    {p},\n" for p in parts) + f"{indent})"


def alias_text(alias: ast.alias) -> str:
    return alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"


def binding(alias: ast.alias, node: ast.stmt) -> str:
    if isinstance(node, ast.Import):
        return alias.asname or alias.name.split(".")[0]
    return alias.asname or alias.name


def loads(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


class Edits:
    """Byte-offset edits on one file (ast offsets are UTF-8 byte columns)."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.starts = [0]
        for index, byte in enumerate(data):
            if byte == 0x0A:
                self.starts.append(index + 1)
        self.edits: list[tuple[int, int, bytes]] = []

    def offset(self, line: int, col: int) -> int:
        return self.starts[line - 1] + col

    def node(self, node: ast.AST, text: str) -> None:
        self.edits.append((self.offset(node.lineno, node.col_offset),
                           self.offset(node.end_lineno, node.end_col_offset), text.encode("utf-8")))

    def lines(self, first: int, last: int, text: str | None) -> None:
        """Replace whole lines first..last (1-based, inclusive); None deletes them."""
        end = self.starts[last] if last < len(self.starts) else len(self.data)
        new = b"" if text is None else (text + "\n").encode("utf-8")
        self.edits.append((self.starts[first - 1], end, new))

    def insert_after_line(self, line: int, text: str) -> None:
        at = self.starts[line] if line < len(self.starts) else len(self.data)
        self.edits.append((at, at, (text + "\n").encode("utf-8")))

    def apply(self) -> bytes:
        data = self.data
        last = None
        for start, end, new in sorted(self.edits, key=lambda e: (e[0], e[1]), reverse=True):
            if last is not None and end > last:
                raise SystemExit(f"overlapping edits at byte {start}")
            data = data[:start] + new + data[end:]
            last = start
        return data


def line_text(data: bytes, starts: list[int], line: int) -> str:
    end = starts[line] - 1 if line < len(starts) else len(data)
    return data[starts[line - 1]:end].decode("utf-8")


def move(target: str, mapping: dict[str, str]) -> int:
    if target not in DOCSTRINGS:
        raise SystemExit(f"no docstring for {target}")
    destination = NET / f"{target}.py"
    if destination.exists():
        raise SystemExit(f"{destination} already exists")
    data = SOURCE.read_bytes()
    text = data.decode("utf-8")
    lines = text.split("\n")
    tree = ast.parse(text)
    body = tree.body
    docstring = body[0] if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) else None
    imports = [n for n in body if isinstance(n, (ast.Import, ast.ImportFrom))]
    header_end = max(n.end_lineno for n in imports)
    statements = [n for n in body if n is not docstring and n not in imports]

    defined = {name for node in statements for name in statement_names(node)}
    bound = {binding(a, n): n for n in imports for a in n.names}

    moving = []
    for node in statements:
        names = statement_names(node)
        if names:
            targets = {mapping.get(n, "UNMAPPED") for n in names}
            if targets == {target}:
                moving.append(node)
            elif target in targets:
                raise SystemExit(f"line {node.lineno}: one statement binds names for {sorted(targets)}")
        else:
            reads = loads(node) & defined
            if reads and {mapping.get(r) for r in reads} == {target}:
                moving.append(node)
    if not moving:
        raise SystemExit(f"nothing in admin_flow.py maps to {target}")
    moving_ids = {id(n) for n in moving}
    staying = [n for n in statements if id(n) not in moving_ids]
    moved_names = {name for node in moving for name in statement_names(node)}

    # what the moved code reads from module scope
    needed = set().union(*(loads(n) for n in moving)) - moved_names
    missing = sorted(n for n in needed & defined if mapping.get(n) != "*")
    if missing:
        raise SystemExit(f"{target} still needs names that stay in admin_flow.py: {', '.join(missing)}")
    wants_logger = "_logger" in needed

    # spans: decorators, the comment lines directly above, and a standalone
    # comment paragraph right above that (reported for review)
    def span(node: ast.stmt) -> tuple[int, int, int | None]:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        while start >= 2 and lines[start - 2].startswith("#"):
            start -= 1
        paragraph = None
        if start >= 3 and lines[start - 2].strip() == "" and lines[start - 3].startswith("#"):
            top = start - 2
            while top >= 2 and lines[top - 2].startswith("#"):
                top -= 1
            if top < 2 or lines[top - 2].strip() == "":
                paragraph = top
                start = top
        return start, node.end_lineno, paragraph

    spans = [span(n) for n in moving]
    for (start, _end, paragraph), node in zip(spans, moving):
        if paragraph is not None:
            print(f"   note: took the comment paragraph at line {paragraph} with {statement_names(node) or node.lineno}")

    # new module
    parts = []
    group = None
    for node in imports:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        keep = [a for a in node.names if binding(a, node) in needed or (wants_logger and binding(a, node) == "logging")]
        if not keep:
            continue
        root = (node.module if isinstance(node, ast.ImportFrom) else node.names[0].name).split(".")[0]
        kind = 0 if root in sys.stdlib_module_names else 2 if root == "netbbs" else 1
        if group is not None and kind != group:
            parts.append("")
        group = kind
        if isinstance(node, ast.Import):
            parts += [f"import {alias_text(a)}" for a in keep]
        else:
            parts.append(format_from("." * node.level + (node.module or ""), [alias_text(a) for a in keep]))
    blocks = ["\n".join(lines[start - 1:end]) for start, end, _ in spans]
    doc = DOCSTRINGS[target]
    new = f'"""{doc}\n"""\n\nfrom __future__ import annotations\n\n' + "\n".join(parts) + "\n"
    if wants_logger:
        new += "\n_logger = logging.getLogger(__name__)\n"
    new += "\n\n" + "\n\n\n".join(blocks) + "\n"
    if wants_logger and "logging" not in {binding(a, n) for n in imports for a in n.names}:
        raise SystemExit("admin_flow.py does not import logging")

    # admin_flow: drop the moved blocks with the blank lines after each
    edits = Edits(data)
    gone = set()
    for start, end, _ in spans:
        last = end
        while last < len(lines) and lines[last].strip() == "":
            last += 1
        gone.update(range(start, last + 1))
    for first, last in _runs(sorted(gone)):
        edits.lines(first, last, None)

    # admin_flow imports: prune what nothing left reads, import what it still needs
    still_read = set().union(*(loads(n) for n in staying))
    target_bound = moved_names | {binding(a, n) for n in imports for a in n.names
                                  if binding(a, n) in needed}
    for node in imports:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        # only what the move made unused: an import nothing read before stays
        keep = [a for a in node.names if binding(a, node) in still_read or binding(a, node) not in needed]
        if len(keep) == len(node.names):
            continue
        if not keep:
            edits.lines(node.lineno, node.end_lineno, None)
        elif isinstance(node, ast.Import):
            edits.lines(node.lineno, node.end_lineno, "\n".join(f"import {alias_text(a)}" for a in keep))
        else:
            edits.lines(node.lineno, node.end_lineno,
                        format_from("." * node.level + (node.module or ""), [alias_text(a) for a in keep]))
    pruned = ({binding(a, n) for n in imports for a in n.names} & needed) - still_read - {"annotations"}
    back = sorted(moved_names & still_read)
    if back:
        edits.insert_after_line(header_end, format_from(f"netbbs.net.{target}", back))
    SOURCE.write_bytes(edits.apply().rstrip(b"\n") + b"\n")
    destination.write_text(new, encoding="utf-8", newline="\n")

    # everyone else
    retarget = moved_names | (pruned & target_bound)
    shared = (needed & set(bound)) - pruned
    moving_reads = set().union(*(loads(n) - set(statement_names(n)) for n in moving))
    patch_keep = (moved_names & still_read) - moving_reads
    patch_both = moved_names & still_read & moving_reads
    changed = rewrite_references(target, retarget, pruned - target_bound, shared, patch_keep, patch_both)

    print(f"moved {len(moving)} statements ({len(moved_names)} names) to {destination.relative_to(ROOT).as_posix()}")
    print(f"admin_flow.py now imports from it: {', '.join(back) or 'nothing'}")
    print(f"dropped from admin_flow.py imports: {', '.join(sorted(pruned)) or 'nothing'}")
    print(f"rewrote {len(changed)} files: {', '.join(changed)}")
    for path in [SOURCE, destination] + [ROOT / c for c in changed]:
        ast.parse(path.read_text(encoding="utf-8"))
    return 0


def _runs(numbers: list[int]):
    run = []
    for n in numbers:
        if run and n != run[-1] + 1:
            yield run[0], run[-1]
            run = []
        run.append(n)
    if run:
        yield run[0], run[-1]


def rewrite_references(target: str, retarget: set[str], lost: set[str], shared: set[str],
                       patch_keep: set[str], patch_both: set[str]) -> list[str]:
    """Point src and test references to moved names at the target module.

    A patch is different from a read: it has to stay on the module whose code
    calls the name. A moved function that only admin_flow's menus call is
    still looked up in admin_flow's globals (through its import of it), so a
    patch on admin_flow keeps reaching it and stays put.
    """
    changed = []
    module = f"netbbs.net.{target}"
    paths = [p for p in sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.py"))
             if p != SOURCE and p.name != f"{target}.py" and p.name != "admin_split.py"]
    for path in paths:
        data = path.read_bytes()
        if b"admin_flow" not in data:
            continue
        tree = ast.parse(data.decode("utf-8"))
        rel = path.relative_to(ROOT).as_posix()
        edits = Edits(data)
        aliases, dotted = set(), False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "netbbs.net":
                aliases |= {a.asname or a.name for a in node.names if a.name == "admin_flow"}
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == MODULE:
                        if a.asname:
                            aliases.add(a.asname)
                        else:
                            dotted = True
        need_from, need_dotted = False, False

        def check(name: str, line: int) -> bool:
            if name in lost:
                print(f"   WARNING {rel}:{line}: {name} is no longer in admin_flow.py and not in {target}")
            if name in shared:
                print(f"   CHECK {rel}:{line}: {name} is used by both admin_flow.py and {target}; "
                      f"patch the module whose code the test drives")
            return name in retarget

        def check_patch(name: str, line: int) -> bool:
            if name in patch_keep:
                return False
            if name in patch_both:
                print(f"   CHECK {rel}:{line}: patch on {name}, which code in both admin_flow.py and "
                      f"{target} calls; left on admin_flow")
                return False
            return check(name, line)

        # `admin_flow._x = fake` is a hand-rolled patch: every reference to
        # `_x` in that file (the saved original, the restore) follows it
        assigned = {
            n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
            and isinstance(n.value, ast.Name) and n.value.id in aliases
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == MODULE:
                move_names = [a for a in node.names if check(a.name, node.lineno)]
                if not move_names:
                    continue
                keep = [a for a in node.names if a not in move_names]
                first = line_text(data, edits.starts, node.lineno)
                indent = first[:len(first) - len(first.lstrip())]
                tail = line_text(data, edits.starts, node.end_lineno).encode("utf-8")[node.end_col_offset:]
                comment = tail.decode("utf-8").strip()
                comment = f"  {comment}" if comment.startswith("#") else ""
                out = []
                if keep:
                    out.append(format_from(MODULE, [alias_text(a) for a in keep], indent))
                out.append(format_from(module, [alias_text(a) for a in move_names], indent))
                out[0] += comment if "\n" not in out[0] else ""
                edits.lines(node.lineno, node.end_lineno, "\n".join(out))
            elif isinstance(node, ast.Attribute):
                value = node.value
                decide = check_patch if node.attr in assigned else check
                if isinstance(value, ast.Name) and value.id in aliases and decide(node.attr, node.lineno):
                    edits.node(value, target)
                    need_from = True
                elif dotted and ast.unparse(value) == MODULE and check(node.attr, node.lineno):
                    edits.node(value, module)
                    need_dotted = True
            elif isinstance(node, ast.Call) and len(node.args) >= 2:
                first_arg, second = node.args[0], node.args[1]
                if isinstance(first_arg, ast.Name) and first_arg.id in aliases \
                        and isinstance(second, ast.Constant) and isinstance(second.value, str) \
                        and check_patch(second.value, node.lineno):
                    edits.node(first_arg, target)
                    need_from = True
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value.startswith(MODULE + ".") \
                    and check_patch(node.value[len(MODULE) + 1:].split(".")[0], node.lineno):
                segment = data[edits.offset(node.lineno, node.col_offset):
                               edits.offset(node.end_lineno, node.end_col_offset)].decode("utf-8")
                edits.node(node, segment.replace(MODULE + ".", module + ".", 1))
        if not edits.edits:
            continue
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        has_from = any(isinstance(n, ast.ImportFrom) and n.module == "netbbs.net"
                       and any(a.name == target and a.asname is None for a in n.names) for n in top)
        has_dotted = any(isinstance(n, ast.Import) and any(a.name == module for a in n.names) for n in top)
        anchor = None
        for n in top:
            if (isinstance(n, ast.ImportFrom) and n.module == "netbbs.net"
                    and any(a.name == "admin_flow" for a in n.names)) or \
                    (isinstance(n, ast.Import) and any(a.name == MODULE for a in n.names)):
                anchor = n
        anchor = anchor or (top[-1] if top else None)
        if need_from and not has_from:
            edits.insert_after_line(anchor.end_lineno if anchor else 0, f"from netbbs.net import {target}")
        if need_dotted and not has_dotted:
            edits.insert_after_line(anchor.end_lineno if anchor else 0, f"import {module}")
        path.write_bytes(edits.apply())
        changed.append(rel)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rep = sub.add_parser("report", help="what the next move involves")
    rep.add_argument("target", nargs="?", help="one target module, e.g. admin_trust")
    mov = sub.add_parser("move", help="move one target's names out of admin_flow.py")
    mov.add_argument("target", help="the target module, e.g. admin_trust")
    args = parser.parse_args(argv)

    order, mapping = load_map()
    if args.command == "move":
        if args.target not in order or args.target == "admin_flow":
            parser.error(f"unknown target {args.target}")
        return move(args.target, mapping)
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
