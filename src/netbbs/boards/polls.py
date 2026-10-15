"""
Polls on message boards (issue #1299, decided in the design doc; built by
#1332).

A poll is an ordinary post plus three side tables keyed by its root post
(`posts.root_post_id`): `board_polls` (the choice mode, the closing time,
when results show), `board_poll_options` (2 to 10 answers, in order) and
`board_poll_votes` (one vote per voter). The post's subject is the
question and its body introduces the poll. The precedent is #842's
`post_file_refs`: rows beside the post rather than in its text, no foreign
keys to `posts` (which migrations rebuild), and one cleanup,
`forget_orphaned_polls_without_commit`, called by every hard delete of
posts rows -- a rejected poll, an expired one swept away, a deleted
board.

- **Who votes:** anyone who may post on the board, the gate a reply takes.
  A vote is stored per account and can be changed until the poll closes.
- **Secret ballot:** no function here returns who voted what, except a
  voter's own vote. Screens show totals and the number of voters. The
  database does hold each account's vote, and the handbooks say so.
- **Locking:** the answers, the choice mode and the results setting lock
  at the first vote; the closing time stays editable while the poll is
  open. A text edit is an ordinary revision of the post.
- **Closing:** at its closing time, or earlier by `close_poll` (the author
  or a moderator), a withdrawal or a takedown. A closed poll stays closed.
  A withdrawn or removed post's totals are hidden.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from netbbs.moderation import BoardPermission, has_permission
from netbbs.storage.database import Database
from netbbs.timeutil import parse_utc_iso, utc_iso, utc_now_iso

if TYPE_CHECKING:
    from netbbs.auth.users import User
    from netbbs.boards.boards import Board

MIN_POLL_OPTIONS = 2
MAX_POLL_OPTIONS = 10
# An answer is a line of a list, not a paragraph.
MAX_POLL_OPTION_BYTES = 200
DEFAULT_POLL_DAYS = 7
MAX_POLL_DAYS = 365

RESULTS_AFTER_VOTE = "after_vote"
RESULTS_AFTER_CLOSE = "after_close"
RESULTS_MODES = (RESULTS_AFTER_VOTE, RESULTS_AFTER_CLOSE)


class PollError(Exception):
    """A poll refused: a bad answer list, a vote on a closed poll, a vote
    by someone who may not post here."""


@dataclass(frozen=True)
class PollSpec:
    """What a poll asks, as its author set it: the answers, how many of them
    a voter may pick (1 for one answer), when it closes (`utc_now_iso`
    form) and when results show (`RESULTS_MODES`)."""

    options: tuple[str, ...]
    max_choices: int
    closes_at: str
    results: str = RESULTS_AFTER_VOTE


@dataclass(frozen=True)
class Poll:
    """A stored poll. `closed_at` is an early close; `link_root_id` the root
    post's Link content id where the poll travels; `dropped_votes` how many
    carried votes the per-poll row cap turned away."""

    root_post_id: str
    options: tuple[str, ...]
    max_choices: int
    closes_at: str
    results: str
    closed_at: str | None = None
    link_root_id: str | None = None
    dropped_votes: int = 0

    @property
    def spec(self) -> PollSpec:
        return PollSpec(self.options, self.max_choices, self.closes_at, self.results)


@dataclass(frozen=True)
class PollTally:
    """A poll's results as this node counts them: votes per answer, in the
    answers' order; how many voters were counted; and how many votes were
    not (dated after the close, or turned away -- see `tally`)."""

    counts: tuple[int, ...]
    voters: int
    not_counted: int = 0


def default_closes_at(days: int = DEFAULT_POLL_DAYS) -> str:
    """`days` from now, in storage form."""
    return utc_iso(parse_utc_iso(utc_now_iso()) + datetime.timedelta(days=days))


def _moment(iso: str) -> datetime.datetime:
    return parse_utc_iso(iso)


def validate_poll_spec(spec: PollSpec, *, now: str | None = None) -> None:
    """Refuse a poll that cannot be asked: fewer than two answers or more
    than ten, a blank, over-long or repeated answer, a choice count outside
    1 to the number of answers, an unknown results setting, or a closing
    time not in the future or more than `MAX_POLL_DAYS` ahead."""
    options = spec.options
    if len(options) < MIN_POLL_OPTIONS:
        raise PollError(f"a poll needs at least {MIN_POLL_OPTIONS} answers")
    if len(options) > MAX_POLL_OPTIONS:
        raise PollError(f"a poll has at most {MAX_POLL_OPTIONS} answers")
    seen = set()
    for option in options:
        if not option.strip():
            raise PollError("an answer is blank")
        size = len(option.encode("utf-8"))
        if size > MAX_POLL_OPTION_BYTES:
            raise PollError(f"an answer is too long: {size} bytes (max {MAX_POLL_OPTION_BYTES})")
        key = option.strip().casefold()
        if key in seen:
            raise PollError(f"the answer {option.strip()!r} is there twice")
        seen.add(key)
    if not 1 <= spec.max_choices <= len(options):
        raise PollError(f"a voter may pick 1 to {len(options)} answers, not {spec.max_choices}")
    if spec.results not in RESULTS_MODES:
        raise PollError(f"unknown results setting {spec.results!r}")
    try:
        closes = _moment(spec.closes_at)
    except ValueError as exc:
        raise PollError(f"unreadable closing time {spec.closes_at!r}") from exc
    current = _moment(now or utc_now_iso())
    if closes <= current:
        raise PollError("the closing date has already passed")
    if closes > current + datetime.timedelta(days=MAX_POLL_DAYS, minutes=1):
        raise PollError(f"a poll closes within {MAX_POLL_DAYS} days")


def write_poll_without_commit(db: Database, root_post_id: str, spec: PollSpec) -> None:
    """Store `spec` as the poll of the post `root_post_id`, in the caller's
    transaction: `netbbs.boards.posts.create_post` writes it with the
    post."""
    db.connection.execute(
        "INSERT INTO board_polls (root_post_id, max_choices, closes_at, results) VALUES (?, ?, ?, ?)",
        (root_post_id, spec.max_choices, spec.closes_at, spec.results),
    )
    db.connection.executemany(
        "INSERT INTO board_poll_options (root_post_id, position, label) VALUES (?, ?, ?)",
        [(root_post_id, position, label.strip()) for position, label in enumerate(spec.options)],
    )


def _has_poll_tables(db: Database) -> bool:
    """Whether this schema has the poll tables: posts are written and
    deleted by migrations older than the one that adds them."""
    return db.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'board_polls'"
    ).fetchone() is not None


def get_poll(db: Database, root_post_id: str) -> Poll | None:
    """The poll of the post whose root is `root_post_id`, or `None` for a
    post without one."""
    if not _has_poll_tables(db):
        return None
    row = db.connection.execute("SELECT * FROM board_polls WHERE root_post_id = ?", (root_post_id,)).fetchone()
    if row is None:
        return None
    options = tuple(
        option["label"]
        for option in db.connection.execute(
            "SELECT label FROM board_poll_options WHERE root_post_id = ? ORDER BY position", (root_post_id,)
        ).fetchall()
    )
    return Poll(
        root_post_id=row["root_post_id"],
        options=options,
        max_choices=row["max_choices"],
        closes_at=row["closes_at"],
        results=row["results"],
        closed_at=row["closed_at"],
        link_root_id=row["link_root_id"],
        dropped_votes=row["dropped_votes"],
    )


def poll_root_ids(db: Database, root_post_ids: list[str]) -> set[str]:
    """Those of `root_post_ids` that are polls -- for a post list's marker."""
    if not root_post_ids or not _has_poll_tables(db):
        return set()
    marks = ",".join("?" * len(root_post_ids))
    return {
        row["root_post_id"]
        for row in db.connection.execute(
            f"SELECT root_post_id FROM board_polls WHERE root_post_id IN ({marks})", root_post_ids
        ).fetchall()
    }


def forget_orphaned_polls_without_commit(db: Database) -> None:
    """Remove the polls, answers and votes of posts no longer in `posts`:
    the cleanup every hard delete of posts rows calls before it commits,
    beside `netbbs.file_refs.forget_orphaned_post_refs_without_commit`."""
    if not _has_poll_tables(db):
        return
    for table in ("board_polls", "board_poll_options", "board_poll_votes"):
        db.connection.execute(
            f"DELETE FROM {table} WHERE root_post_id NOT IN (SELECT post_id FROM posts)"
        )


def _current_revision(db: Database, root_post_id: str):
    return db.connection.execute(
        "SELECT * FROM posts WHERE root_post_id = ? AND status = 'approved' ORDER BY id DESC LIMIT 1",
        (root_post_id,),
    ).fetchone()


def poll_hidden(db: Database, poll: Poll) -> bool:
    """Whether the poll's post is withdrawn or removed now, which hides its
    totals (the design doc's moderation rule)."""
    current = _current_revision(db, poll.root_post_id)
    if current is None:
        return False
    return current["tombstoned_at"] is not None or bool(current["withdrawn"])


def closing_moment(poll: Poll) -> str:
    """When the poll closed or closes: its early close, if it had one
    before its closing time."""
    if poll.closed_at is not None and _moment(poll.closed_at) < _moment(poll.closes_at):
        return poll.closed_at
    return poll.closes_at


def poll_closed(db: Database, poll: Poll, *, now: str | None = None) -> bool:
    """Whether the poll takes no more votes: past its closing time, closed
    early, or its post withdrawn or removed."""
    if poll.closed_at is not None:
        return True
    if _moment(now or utc_now_iso()) >= _moment(poll.closes_at):
        return True
    return poll_hidden(db, poll)


def poll_approved(db: Database, poll: Poll) -> bool:
    """Whether the poll's post has been approved: a held poll cannot be
    voted on (the design doc's moderated-board rule)."""
    return db.connection.execute(
        "SELECT 1 FROM posts WHERE root_post_id = ? AND status = 'approved' LIMIT 1", (poll.root_post_id,)
    ).fetchone() is not None


def has_votes(db: Database, poll: Poll) -> bool:
    return db.connection.execute(
        "SELECT 1 FROM board_poll_votes WHERE root_post_id = ? LIMIT 1", (poll.root_post_id,)
    ).fetchone() is not None


def answers_locked(db: Database, poll: Poll) -> bool:
    """Whether the answers, the choice mode and the results setting are
    fixed: once anyone has voted, and from the start on a poll that travels
    over the Link, whose signed post carries them."""
    return poll.link_root_id is not None or has_votes(db, poll)


def _parse_choices(raw: str, option_count: int) -> tuple[int, ...] | None:
    try:
        choices = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(choices, list) or not choices:
        return None
    if any(not isinstance(c, int) or isinstance(c, bool) or not 0 <= c < option_count for c in choices):
        return None
    if len(set(choices)) != len(choices):
        return None
    return tuple(sorted(choices))


def user_vote(db: Database, poll: Poll, user: User) -> tuple[int, ...] | None:
    """`user`'s own vote, as answer positions -- the only vote any function
    here names a voter for -- or `None` if they have not voted."""
    row = db.connection.execute(
        "SELECT choices FROM board_poll_votes WHERE root_post_id = ? AND user_id = ?", (poll.root_post_id, user.id)
    ).fetchone()
    if row is None:
        return None
    return _parse_choices(row["choices"], len(poll.options))


def tally(db: Database, poll: Poll) -> PollTally:
    """The poll's results on this node: each answer's count and how many
    voters were counted. A vote dated after the poll closed is not counted;
    `not_counted` says how many were left out, with those the per-poll row
    cap turned away."""
    counts = [0] * len(poll.options)
    voters = 0
    not_counted = poll.dropped_votes
    closes = _moment(closing_moment(poll))
    rows = db.connection.execute(
        "SELECT choices, voted_at FROM board_poll_votes WHERE root_post_id = ? ORDER BY id", (poll.root_post_id,)
    ).fetchall()
    for row in rows:
        choices = _parse_choices(row["choices"], len(poll.options))
        try:
            late = _moment(row["voted_at"]) > closes
        except ValueError:
            late = True
        if choices is None or len(choices) > poll.max_choices or late:
            not_counted += 1
            continue
        voters += 1
        for choice in choices:
            counts[choice] += 1
    return PollTally(tuple(counts), voters, not_counted)


def _require_may_vote(db: Database, user: User, board: Board) -> None:
    """The gate a reply takes on this node: the board's write level or a
    write grant, its age requirement and its name requirement."""
    from netbbs.attestation import meets_name_requirement
    from netbbs.communities import get_effective_name_requirement, meets_resource_age, meets_write_gate

    if not meets_write_gate(db, user, board):
        raise PollError("only callers who may post on this board can vote")
    if not meets_resource_age(db, user, board):
        raise PollError("this board has an age requirement you do not meet")
    if not meets_name_requirement(db, user, get_effective_name_requirement(db, board)):
        raise PollError("voting here needs a verified name")


def _refuse_if_board_unavailable(db: Database, board: Board) -> None:
    row = db.connection.execute("SELECT * FROM boards WHERE id = ?", (board.id,)).fetchone()
    if row is None:
        raise PollError("this message board no longer exists")
    if row["link_hidden_at"] is not None:
        raise PollError(f"board {row['name']!r} is no longer available on this node")
    if row["link_closed_at"] is not None:
        raise PollError(f"board {row['name']!r} is closed")


def cast_vote(db: Database, poll: Poll, board: Board, voter: User, choices) -> tuple[int, ...]:
    """Record `voter`'s vote on `poll`, replacing any earlier one: `choices`
    are answer positions, one to `poll.max_choices` of them. Refused on a
    held poll, a closed one, or for a caller who may not post here.
    Returns the vote as stored."""
    picked = sorted(set(choices))
    if not picked:
        raise PollError("pick an answer to vote")
    if len(picked) > poll.max_choices:
        raise PollError(
            "pick one answer" if poll.max_choices == 1 else f"pick at most {poll.max_choices} answers"
        )
    if any(not 0 <= choice < len(poll.options) for choice in picked):
        raise PollError("no such answer")
    _refuse_if_board_unavailable(db, board)
    current = get_poll(db, poll.root_post_id)
    if current is None:
        raise PollError("this poll no longer exists")
    if not poll_approved(db, current):
        raise PollError("this poll waits for a moderator: it can be voted on once it is approved")
    if poll_closed(db, current):
        raise PollError("this poll is closed")
    _require_may_vote(db, voter, board)
    now = utc_now_iso()
    encoded = json.dumps(picked)
    updated = db.connection.execute(
        "UPDATE board_poll_votes SET choices = ?, voted_at = ? WHERE root_post_id = ? AND user_id = ?",
        (encoded, now, poll.root_post_id, voter.id),
    ).rowcount
    if not updated:
        db.connection.execute(
            "INSERT INTO board_poll_votes (root_post_id, user_id, choices, voted_at) VALUES (?, ?, ?, ?)",
            (poll.root_post_id, voter.id, encoded, now),
        )
    db.connection.commit()
    return tuple(picked)


def may_manage_poll(db: Database, poll: Poll, post_author_user_id: int | None, board: Board, user: User) -> bool:
    """The poll's author, or a moderator of the board (who may edit posts
    or take them down there): who may close it or change its closing
    date."""
    if post_author_user_id is not None and post_author_user_id == user.id:
        return True
    return has_permission(db, user, object_type="board", object_id=board.id, permission=BoardPermission.EDIT) or (
        has_permission(db, user, object_type="board", object_id=board.id, permission=BoardPermission.DELETE)
    )


def close_poll(db: Database, poll: Poll, post_author_user_id: int | None, board: Board, *, closed_by: User) -> str:
    """[C]lose poll: it takes no more votes from now. For its author and the
    board's moderators. Returns the closing time."""
    if not may_manage_poll(db, poll, post_author_user_id, board, closed_by):
        raise PollError("only the poll's author or a moderator can close it")
    _refuse_if_board_unavailable(db, board)
    current = get_poll(db, poll.root_post_id)
    if current is None:
        raise PollError("this poll no longer exists")
    if poll_closed(db, current):
        raise PollError("this poll is already closed")
    now = utc_now_iso()
    db.connection.execute(
        "UPDATE board_polls SET closed_at = ? WHERE root_post_id = ? AND closed_at IS NULL", (now, poll.root_post_id)
    )
    db.connection.commit()
    return now


def close_poll_for_removal_without_commit(db: Database, root_post_id: str) -> None:
    """A withdrawal or a takedown closes the post's poll, if it has one, in
    the caller's transaction. It stays closed if the post is edited again."""
    if not _has_poll_tables(db):
        return
    db.connection.execute(
        "UPDATE board_polls SET closed_at = ? WHERE root_post_id = ? AND closed_at IS NULL",
        (utc_now_iso(), root_post_id),
    )


def update_poll(
    db: Database,
    poll: Poll,
    post_author_user_id: int | None,
    board: Board,
    spec: PollSpec,
    *,
    changed_by: User,
) -> Poll:
    """Change an open poll to `spec`. The closing time may change while the
    poll is open; the answers, the choice mode and the results setting only
    until the first vote (`answers_locked`). Refused for anyone but the
    author and the board's moderators."""
    if not may_manage_poll(db, poll, post_author_user_id, board, changed_by):
        raise PollError("only the poll's author or a moderator can change it")
    _refuse_if_board_unavailable(db, board)
    current = get_poll(db, poll.root_post_id)
    if current is None:
        raise PollError("this poll no longer exists")
    if current.spec == spec:
        return current
    if poll_closed(db, current):
        raise PollError("this poll is closed")
    locked = answers_locked(db, current)
    answers_changed = (
        tuple(o.strip() for o in spec.options) != current.options
        or spec.max_choices != current.max_choices
        or spec.results != current.results
    )
    if locked and answers_changed:
        raise PollError(
            "the answers can no longer change: "
            + ("the poll travels to other BBSes" if current.link_root_id is not None else "someone has voted")
        )
    validate_poll_spec(spec)
    db.connection.execute(
        "UPDATE board_polls SET max_choices = ?, closes_at = ?, results = ? WHERE root_post_id = ?",
        (spec.max_choices, spec.closes_at, spec.results, poll.root_post_id),
    )
    if answers_changed:
        db.connection.execute("DELETE FROM board_poll_options WHERE root_post_id = ?", (poll.root_post_id,))
        db.connection.executemany(
            "INSERT INTO board_poll_options (root_post_id, position, label) VALUES (?, ?, ?)",
            [(poll.root_post_id, position, label.strip()) for position, label in enumerate(spec.options)],
        )
    db.connection.commit()
    return get_poll(db, poll.root_post_id)
