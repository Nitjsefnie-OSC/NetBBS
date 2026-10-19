"""A bot's titled block (design doc §16, issue #1373).

A bot may answer with a block instead of a line: its own title and up to 12
rows, each an optional short label and a text, drawn like `/who`
(`netbbs.net.chat_reply`). The bot sends plain text only; the renderer owns
every colour, and the host adds the `[BOT] Name.bot` part of the title, so a
block can never claim another author.

Beside every block the host builds one plain fallback line,
`Title: row; row; row`, cut to 500 characters. It is the message body: what
an older node shows, what search matches and what the hourly limit counts.
The bot never supplies a fallback of its own that could disagree.

This module is the one place the shape is judged: a bot's block through
`parse_block` (a refusal the bot is told), a stored or peer's block through
`block_from_wire` (a malformed one is `None`, so the fallback line shows).

A block may mark its labels as command syntax with `"labels": "commands"`,
so a bot's command list is drawn the way `/help` draws its own; that is the
only value, and without it the labels are plain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from netbbs.rendering import sanitize_text

MAX_TITLE = 40
MAX_ROWS = 12
MAX_LABEL = 24
MAX_TEXT = 200
#: The fallback line's limit: a bot line's.
MAX_FALLBACK = 500
#: What a block's optional `labels` may say: its labels are command syntax.
LABELS_COMMANDS = "commands"


class BlockError(ValueError):
    """A block shape the host refuses, safe to show the bot's operator."""


@dataclass(frozen=True)
class BotBlock:
    title: str
    # (label, text) pairs; an empty label is a row without one.
    rows: tuple[tuple[str, str], ...]
    # `LABELS_COMMANDS` when the labels are command syntax, else None.
    labels: str | None = None

    def to_wire(self) -> dict:
        wire = {"title": self.title,
                "rows": [{"label": label, "text": text} if label else {"text": text} for label, text in self.rows]}
        if self.labels is not None:
            wire["labels"] = self.labels
        return wire

    def to_json(self) -> str:
        return json.dumps(self.to_wire(), ensure_ascii=False, separators=(",", ":"))

    def fallback(self) -> str:
        """`Title: row; row; row`, cut to 500 characters."""
        rows = "; ".join(f"{label} {text}" if label else text for label, text in self.rows)
        line = f"{self.title}: {rows}"
        return line if len(line) <= MAX_FALLBACK else line[:MAX_FALLBACK - 1].rstrip() + "…"


def _plain(value, what: str, limit: int, *, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if not isinstance(value, str):
        raise BlockError(f"A block's {what} must be a string.")
    if optional and value == "":
        return ""
    if not value.strip() or len(value) > limit:
        raise BlockError(f"A block's {what} must contain 1-{limit} characters.")
    if any(c in value for c in "\r\n\t") or sanitize_text(value) != value:
        raise BlockError(f"A block's {what} must be plain text on one line, without terminal controls.")
    return value


def parse_block(value) -> BotBlock:
    """A bot's block, checked: `{"title": ..., "rows": [{"label": ..., "text": ...}]}`,
    a title of 1-40 characters and 1-12 rows of an optional label of up to
    24 characters and a text of 1-200, plain text only, and an optional
    `"labels": "commands"`."""
    if not isinstance(value, dict) or set(value) - {"title", "rows", "labels"} or "rows" not in value:
        raise BlockError("A block is an object with a title, rows and optional labels.")
    if "labels" in value and value["labels"] != LABELS_COMMANDS:
        raise BlockError(f"A block's labels, when given, must be \"{LABELS_COMMANDS}\".")
    title = _plain(value.get("title"), "title", MAX_TITLE)
    rows = value["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise BlockError(f"A block has 1-{MAX_ROWS} rows.")
    parsed = []
    for row in rows:
        if not isinstance(row, dict) or "text" not in row or set(row) - {"label", "text"}:
            raise BlockError("A block row is an object with a text and an optional label.")
        parsed.append((_plain(row.get("label"), "row label", MAX_LABEL, optional=True),
                       _plain(row["text"], "row text", MAX_TEXT)))
    return BotBlock(title, tuple(parsed), value.get("labels"))


def block_from_wire(value) -> BotBlock | None:
    """A stored block or a peer's: `None` when it is absent or malformed,
    so the line's fallback body is shown instead (the poll precedent)."""
    if value is None:
        return None
    try:
        return parse_block(value)
    except BlockError:
        return None


def block_from_json(raw: str | None) -> BotBlock | None:
    if not raw:
        return None
    try:
        return block_from_wire(json.loads(raw))
    except (ValueError, RecursionError):
        return None
