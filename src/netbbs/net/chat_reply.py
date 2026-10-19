"""Chat command replies as titled blocks (design doc, chat commands).

A reply to `/list`, `/who`, `/whois`, `/mrc` and their kin is one block in
the scrolling area: a rule with the title set into it (and a count or a
time at its right end), then rows whose columns line up, the last column
wrapping under itself. Flat unaligned lines read as a wall of text between
chat traffic; a block is told apart from conversation at a glance.

Everything here takes sanitized plain text and styles it last ("sanitize
before styling"); a cell is `(text, color)`, never a pre-colored string, so
wrapping measures what the caller sees. A cell's text may instead be a
sequence of `(text, color)` segments -- `nick@site` with the site in its own
colour -- each segment's `None` taking the cell's colour -- or `Styled`,
plain text a helper has already styled from its sanitized form (a bot's
command syntax, drawn by `/help`'s own helpers).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from netbbs.rendering import colored
from netbbs.rendering.theme import METADATA_COLOR, RULE_COLOR
from netbbs.rendering.width import display_width, truncate_to_width, wrap_to_width

#: Widest a block draws, however wide the terminal: a rule across 200
#: columns is a line of noise, and the rows never need more.
MAX_BLOCK_WIDTH = 78

#: The narrowest the wrapping last column may get before it moves to a
#: line of its own beneath the aligned ones.
MIN_LAST_COLUMN = 20

Segments = Sequence[tuple[str, "int | None"]]


@dataclass(frozen=True)
class Styled:
    """A cell's text already styled: `plain` is what is measured and
    wrapped, `styled` what is drawn. `styled` must be `plain` with styling
    added and nothing else, built from sanitized text; a last cell too long
    for its room wraps as `plain`, in the cell's colour."""
    plain: str
    styled: str


Cell = tuple["str | Segments | Styled", "int | None"]


def _plain(text: "str | Segments | Styled") -> str:
    if isinstance(text, Styled):
        return text.plain
    return text if isinstance(text, str) else "".join(part for part, _color in text)


def _styled(text: "str | Segments | Styled", color: int | None) -> str:
    if isinstance(text, Styled):
        return text.styled
    if isinstance(text, str):
        return _style(text, color)
    return "".join(_style(part, own if own is not None else color) for part, own in text)


def block_width(terminal_width: int) -> int:
    return max(1, min(terminal_width, MAX_BLOCK_WIDTH))


def title_rule(
    title: str, *, width: int, unicode_style: bool, title_color: int,
    count: int | None = None, right: str | None = None,
) -> str:
    """`── Channels ──────────── 3 ──`: `right` (a time) wins over `count`."""
    char = "─" if unicode_style else "-"
    tail = right if right is not None else (str(count) if count is not None else "")
    tail_text = f" {tail} {char * 2}" if tail else ""
    lead = char * 2 + " "
    room = width - display_width(lead) - display_width(tail_text) - 3
    shown = truncate_to_width(title, room) if room >= 1 else ""
    fill = max(2, width - display_width(lead) - display_width(shown) - 1 - display_width(tail_text))
    rule = colored(lead, fg_color=RULE_COLOR) + colored(shown, fg_color=title_color, bold=True)
    rule += colored(" " + char * fill, fg_color=RULE_COLOR)
    if tail:
        rule += " " + colored(tail, fg_color=METADATA_COLOR) + " " + colored(char * 2, fg_color=RULE_COLOR)
    return rule



def footer_rule(text: str, *, width: int, unicode_style: bool) -> str:
    """`──────── page 1 of 4 · Enter shows more ──`: a rule closing a block,
    `text` set in near its right end."""
    char = "─" if unicode_style else "-"
    shown = truncate_to_width(text, max(1, width - 6))
    fill = max(2, width - display_width(shown) - 4)
    return (colored(char * fill + " ", fg_color=RULE_COLOR) + colored(shown, fg_color=METADATA_COLOR)
            + colored(" " + char * 2, fg_color=RULE_COLOR))

def _style(text: str, color: int | None) -> str:
    return colored(text, fg_color=color) if color is not None and text else text


def table_rows(rows: Sequence[Sequence[Cell]], *, width: int) -> list[str]:
    """Rows of cells, one space in from the edge, two between columns.

    Every row has the same number of cells (an empty one is `("", None)`).
    Every column but the last is padded to its widest cell; the last wraps
    at the width left over and continues under itself. A first column too
    wide to leave the last one `MIN_LAST_COLUMN` (a long MRC `nick@site`)
    is capped, and a cell wider than its cap stands on a line of its own
    with the rest of its row beneath it, aligned -- never cut."""
    if not rows:
        return []
    columns = max(len(row) for row in rows)
    widths = [0] * columns
    for row in rows:
        for index, (text, _color) in enumerate(row[:-1]):
            widths[index] = max(widths[index], display_width(_plain(text)))
    # Columns that only some rows reach still need their space reserved.
    budget = width - 1 - MIN_LAST_COLUMN - 2 * (columns - 1)
    while sum(widths[:-1]) > budget and max(widths[:-1], default=0) > 8:
        widest = widths.index(max(widths[:-1]))
        widths[widest] -= 1
    lines: list[str] = []
    for row in rows:
        lines.extend(_row_lines(row, widths, width))
    return lines


def _row_lines(row: Sequence[Cell], widths: list[int], width: int) -> list[str]:
    lines: list[str] = []
    line = " "
    column = 1
    *leading, (last_text, last_color) = row
    for index, (text, color) in enumerate(leading):
        cell_width = widths[index]
        shown = display_width(_plain(text))
        if shown > cell_width:
            # Over its cap: the cell alone, then the row carries on aligned.
            lines.append((line + _styled(text, color)).rstrip())
            line = " " * (column + cell_width + 2)
        else:
            line += _styled(text, color) + " " * (cell_width - shown + 2)
        column += cell_width + 2
    room = width - column
    if room < MIN_LAST_COLUMN and leading:
        # An overflowing cell may already have flushed this row's content,
        # leaving only padding: that is no line of its own.
        if line.strip():
            lines.append(line.rstrip())
        indent = 3
        line, room = " " * indent, width - indent
        column = indent
    if not isinstance(last_text, str):
        if display_width(_plain(last_text)) <= room:
            lines.append(line + _styled(last_text, last_color))
            return lines
        # Too long to keep its segments: wrapped, in the cell's colour.
        last_text = _plain(last_text)
    wrapped = wrap_to_width(last_text, max(1, room)) if last_text else []
    if not wrapped:
        if line.strip():
            lines.append(line.rstrip())
        return lines
    lines.append(line + _style(wrapped[0], last_color))
    lines.extend(" " * column + _style(part, last_color) for part in wrapped[1:])
    return lines


def paragraph(text: str, *, width: int, color: int | None = None) -> list[str]:
    """Prose inside a block (a comma-separated roster, a closing note), one
    space in from the edge like every row."""
    return [" " + _style(part, color) for part in wrap_to_width(text, max(1, width - 1))] if text else []
