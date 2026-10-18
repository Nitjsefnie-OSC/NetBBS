"""
The oneliner wall (issue #1391, design doc §3.3): one short line a caller
leaves for the callers after them, shown under Recent callers at logon and
in full from the `[R]ecent callers` screen.

A table of its own (`oneliners`), not a board: a line has no subject, no
replies, no unread state and no approval queue, and a board would have
needed a "not this board" exception on every board surface.

The limits are fixed here, like the chat bell's: a line is at most
`MAX_ONELINER_COLUMNS` display columns, a caller adds at most one line per
call and `DAILY_ONELINER_LIMIT` per account in any 24 hours (which also caps
every guest together, since they share one account), counted from a day's log
of lines added (`oneliner_additions`) so that deleting a line gives no quota
back, and the wall keeps its
newest `MAX_KEPT_ONELINERS` lines. Who may write is a node-wide level
(`netbbs.config.get_oneliner_min_level`, default 0); reading is open.

Authors delete their own lines; a SysOp or a "moderate everything" staff
member deletes any, and that deletion is audited with the text. A line
outlives its author's account (`author_user_id ON DELETE SET NULL`, the
label kept beside it), as a post does.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from netbbs.auth.users import User
from netbbs.config import get_config, get_oneliner_min_level, set_config
from netbbs.moderation.log import record_action_without_commit
from netbbs.permissions import meets_level
from netbbs.rendering.sanitize import sanitize_text
from netbbs.rendering.width import display_width
from netbbs.staff import moderates_everything
from netbbs.storage.database import Database
from netbbs.timeutil import utc_iso, utc_now_iso

#: The longest line, in display columns: beside a 12-column name it fits
#: the 78-wide frame without cutting at 80 columns.
MAX_ONELINER_COLUMNS = 60
#: Lines one account may add in any 24 hours.
DAILY_ONELINER_LIMIT = 3
#: The wall keeps this many of its newest lines; older ones are pruned
#: when a line is added, so the table cannot grow without bound.
MAX_KEPT_ONELINERS = 200

_ENABLED_KEY = "oneliners_enabled"
_PLAIN_KEY = "oneliners_plain"
_WHITESPACE = re.compile(r"\s+")


class OnelinerError(ValueError):
    """A line that cannot be added or deleted; the message is for the caller."""


@dataclass(frozen=True)
class Oneliner:
    id: int
    author_user_id: int | None
    author_label: str
    text: str
    created_at: str


def oneliners_enabled(db: Database) -> bool:
    """Whether the wall is shown after login. The `[R]ecent callers`
    screen shows it whatever this says."""
    return get_config(db, _ENABLED_KEY, default="1") == "1"


def set_oneliners_enabled(db: Database, enabled: bool) -> None:
    set_config(db, _ENABLED_KEY, "1" if enabled else "0")


def oneliners_plain(db: Database) -> bool:
    """The wall's own plain style, used when it is drawn without the
    caller roll; sharing the frame, it follows Recent callers' style."""
    return get_config(db, _PLAIN_KEY, default="0") == "1"


def set_oneliners_plain(db: Database, plain: bool) -> None:
    set_config(db, _PLAIN_KEY, "1" if plain else "0")


def normalize_oneliner(text: str) -> str:
    """`text` as the wall stores it: control and bidi characters removed,
    every run of whitespace (tabs and newlines included) one space, and
    trimmed."""
    return _WHITESPACE.sub(" ", sanitize_text(text, allow_newlines=True)).strip()


def may_write_oneliners(db: Database, user: User) -> bool:
    """Whether `user` meets the node's oneliner level. Below it the
    `[A]dd a line` key is not offered at all."""
    return meets_level(user, get_oneliner_min_level(db))


def _row(row) -> Oneliner:
    return Oneliner(
        id=row["id"],
        author_user_id=row["author_user_id"],
        author_label=row["author_label"],
        text=row["text"],
        created_at=row["created_at"],
    )


def _a_day_ago() -> str:
    return utc_iso(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))


def _lines_in_last_day(db: Database, user: User) -> int:
    """Lines `user` added in the last 24 hours, deleted or pruned ones too."""
    return db.connection.execute(
        "SELECT COUNT(*) FROM oneliner_additions WHERE user_id = ? AND created_at > ?",
        (user.id, _a_day_ago()),
    ).fetchone()[0]


def oneliner_refusal(db: Database, user: User, *, added_this_call: bool) -> str | None:
    """Why `user` may not add a line now, in words for the caller, or `None`."""
    level = get_oneliner_min_level(db)
    if not meets_level(user, level):
        return f"Adding a line to the wall needs level {level}."
    if added_this_call:
        return "You've added a line this call."
    if _lines_in_last_day(db, user) >= DAILY_ONELINER_LIMIT:
        return f"You've added {DAILY_ONELINER_LIMIT} today; the wall takes more tomorrow."
    return None


def add_oneliner(db: Database, user: User, text: str, *, added_this_call: bool) -> Oneliner:
    """Add one line by `user`, newest first on the wall, and prune the
    wall to its newest `MAX_KEPT_ONELINERS`. Raises `OnelinerError` with
    the reason when the line is refused."""
    refusal = oneliner_refusal(db, user, added_this_call=added_this_call)
    if refusal is not None:
        raise OnelinerError(refusal)
    line = normalize_oneliner(text)
    if not line:
        raise OnelinerError("A blank line leaves nothing on the wall.")
    over = display_width(line) - MAX_ONELINER_COLUMNS
    if over > 0:
        raise OnelinerError(
            f"That line is {over} column{'s' if over != 1 else ''} too long ({MAX_ONELINER_COLUMNS} at most)."
        )
    created_at = utc_now_iso()
    cursor = db.connection.execute(
        "INSERT INTO oneliners (author_user_id, author_label, text, created_at) VALUES (?, ?, ?, ?)",
        (user.id, user.username, line, created_at),
    )
    db.connection.execute(
        "DELETE FROM oneliners WHERE id NOT IN (SELECT id FROM oneliners ORDER BY id DESC LIMIT ?)",
        (MAX_KEPT_ONELINERS,),
    )
    # The log only has to remember a day: older entries never count.
    db.connection.execute(
        "INSERT INTO oneliner_additions (user_id, created_at) VALUES (?, ?)", (user.id, created_at)
    )
    db.connection.execute("DELETE FROM oneliner_additions WHERE created_at <= ?", (_a_day_ago(),))
    db.connection.commit()
    return Oneliner(cursor.lastrowid, user.id, user.username, line, created_at)


def list_oneliners(db: Database, *, limit: int | None = None) -> list[Oneliner]:
    """The wall, newest first."""
    rows = db.connection.execute(
        "SELECT * FROM oneliners ORDER BY id DESC LIMIT ?",
        (-1 if limit is None else limit,),
    ).fetchall()
    return [_row(row) for row in rows]


def get_oneliner(db: Database, oneliner_id: int) -> Oneliner | None:
    row = db.connection.execute("SELECT * FROM oneliners WHERE id = ?", (oneliner_id,)).fetchone()
    return _row(row) if row is not None else None


def may_delete_oneliner(user: User, line: Oneliner) -> bool:
    """The author, a SysOp, or a "moderate everything" staff member."""
    return line.author_user_id == user.id or moderates_everything(user)


def may_delete_any_oneliner(user: User) -> bool:
    return moderates_everything(user)


def delete_oneliner(db: Database, actor: User, oneliner_id: int) -> Oneliner:
    """Delete one line. An author's own deletion is not audited; anyone
    else's is, with the text in the detail (design doc §5.2)."""
    line = get_oneliner(db, oneliner_id)
    if line is None:
        raise OnelinerError("That line is no longer on the wall.")
    if not may_delete_oneliner(actor, line):
        raise OnelinerError("You can delete only your own lines.")
    db.connection.execute("BEGIN IMMEDIATE")
    try:
        db.connection.execute("DELETE FROM oneliners WHERE id = ?", (line.id,))
        if line.author_user_id != actor.id:
            record_action_without_commit(
                db,
                actor=actor,
                action="delete_oneliner",
                object_type="oneliner",
                object_id=line.id,
                target_user_id=line.author_user_id,
                detail=f"{line.author_label}: {line.text}",
            )
    except BaseException:
        db.connection.rollback()
        raise
    else:
        db.connection.commit()
    return line
