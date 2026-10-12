"""
The handbooks' menu paths and hotkeys name screens that exist.

The handbooks carry no screenshots, so a renamed menu leaves nothing
visibly stale behind -- only a sentence naming a screen nobody can find.
Text written in the handbooks' notation (docs/README.md, "Formatting the
handbooks") is checked here against the labels the console actually
draws:

- a menu path, `**Users ▸ Held names**`: every step is a label the
  source draws (a menu entry, a screen title, a section heading);
- a hotkey with its label, `<kbd>L</kbd> **Level**`: a menu entry with
  that key and that label exists.

Prose in the older notation (`→`, `[N]ame`) is not checked; it gains the
check as each chapter moves to the new one.
"""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "netbbs"
HANDBOOKS = sorted((ROOT / "docs").glob("NetBBS-*-Handbook.md"))

_PATH = re.compile(r"\*\*([^*\n]+? ▸ [^*\n]+?)\*\*")
_KEYED = re.compile(r"<kbd>([^<]+)</kbd> \*\*([^*\n]+)\*\*")
_FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def _norm(label: str) -> str:
    """`[L]evels (LAST)` and `Levels (LAST)` are the same label."""
    label = re.sub(r"\[(\w)\]", r"\1", label)
    return " ".join(label.split()).casefold()


def _text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _menu_key(call: ast.Call) -> tuple[str, str] | None:
    """`menu_key("L", "evel")` as ("l", "level"), or None when any part is
    computed at run time."""
    parts = [_text(arg) for arg in call.args]
    prefix = next((_text(kw.value) for kw in call.keywords if kw.arg == "prefix"), "")
    if not parts or any(part is None for part in parts) or prefix is None:
        return None
    return parts[0].casefold(), _norm(prefix + "".join(parts))


@cache
def _console_vocabulary() -> tuple[frozenset[str], frozenset[tuple[str, str]]]:
    """Every label the source draws, and every (hotkey, label) pair."""
    labels: set[str] = set()
    keyed: set[tuple[str, str]] = set()

    def add(text: str | None) -> None:
        if text:
            labels.add(_norm(text))

    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                # `title, subtitle = "Your profile", ...` and the policy
                # settings' `GROUP_CARRY = "Carry caps"`.
                for target in node.targets:
                    names = target.elts if isinstance(target, ast.Tuple) else [target]
                    values = node.value.elts if isinstance(node.value, ast.Tuple) else [node.value]
                    for name, value in zip(names, values):
                        if isinstance(name, ast.Name) and (name.id == "title" or name.id.startswith("GROUP_")):
                            add(_text(value))
                continue
            if not isinstance(node, ast.Call):
                continue
            func = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if func == "menu_key":
                pair = _menu_key(node)
                if pair:
                    keyed.add(pair)
                    labels.add(pair[1])
            elif func in {"located", "Section"} and node.args:
                add(_text(node.args[0]))
            elif func == "entered" and len(node.args) > 1:
                add(_text(node.args[1]))
            for keyword in node.keywords:
                if keyword.arg in {"title", "label"}:
                    add(_text(keyword.value))
    return frozenset(labels), frozenset(keyed)


def _prose(handbook: Path) -> list[tuple[int, str]]:
    """The handbook's lines outside code blocks, numbered from 1."""
    text = handbook.read_text(encoding="utf-8")
    text = _FENCE.sub(lambda block: "\n" * block.group().count("\n"), text)
    return list(enumerate(text.splitlines(), start=1))


def _joined(handbook: Path) -> list[tuple[int, str]]:
    """Each paragraph as one line, so a path wrapped across two lines is
    still one path; numbered by its first line."""
    paragraphs: list[tuple[int, str]] = []
    start, lines = 0, []
    for number, line in _prose(handbook) + [(0, "")]:
        if line.strip():
            if not lines:
                start = number
            lines.append(line.strip())
        elif lines:
            paragraphs.append((start, " ".join(lines)))
            lines = []
    return paragraphs


@pytest.mark.parametrize("handbook", HANDBOOKS, ids=lambda path: path.name)
def test_every_menu_path_names_screens_that_exist(handbook):
    labels, _ = _console_vocabulary()
    unknown = [
        f"{handbook.name}:{number}: **{path}** -- no screen or menu entry {step!r}"
        for number, paragraph in _joined(handbook)
        for path in _PATH.findall(paragraph)
        for step in (part.strip() for part in path.split("▸"))
        if _norm(step) not in labels
    ]
    assert not unknown, "\n".join(unknown)


@pytest.mark.parametrize("handbook", HANDBOOKS, ids=lambda path: path.name)
def test_every_hotkey_matches_its_menu_entry(handbook):
    _, keyed = _console_vocabulary()
    unknown = [
        f"{handbook.name}:{number}: <kbd>{key}</kbd> **{label}** -- no menu entry with that key and label"
        for number, paragraph in _joined(handbook)
        for key, label in _KEYED.findall(paragraph)
        if (key.casefold(), _norm(label)) not in keyed
    ]
    assert not unknown, "\n".join(unknown)


def test_the_check_reads_the_console_and_the_handbooks():
    """A vocabulary that silently came up empty would pass every path."""
    labels, keyed = _console_vocabulary()
    assert {"users", "held names", "levels (last)"} <= labels
    assert ("l", "level") in keyed
    assert {path.name for path in HANDBOOKS} >= {"NetBBS-SysOp-Handbook.md", "NetBBS-User-Handbook.md"}


@pytest.mark.parametrize(
    ("line", "complaint"),
    [
        ("Open **Users ▸ Helt names**.", "Helt names"),
        ("Press <kbd>Q</kbd> **Level**.", "<kbd>Q</kbd>"),
    ],
)
def test_a_misnamed_screen_or_key_is_reported(tmp_path, line, complaint):
    handbook = tmp_path / "NetBBS-Test-Handbook.md"
    handbook.write_text(f"# Test\n\n{line}\n\n```sh\n**Not ▸ Checked**\n```\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=re.escape(complaint)):
        if "<kbd>" in line:
            test_every_hotkey_matches_its_menu_entry(handbook)
        else:
            test_every_menu_path_names_screens_that_exist(handbook)
