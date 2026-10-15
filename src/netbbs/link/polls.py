"""
Polls on Linked boards (design doc §16, issue #1299 Decision 4; built by
#1332).

- **The poll travels with its post.** A poll's `board_post` carries an
  optional `"poll"` field: the answers, the choice mode, the closing date
  and the results setting (`poll_field`). Since that root event is
  immutable and votes may already exist elsewhere, a Linked poll's answers
  are fixed when it is posted. Its closing date changes through the
  author's `board_post_edit` or the origin's `board_post_moderator_edit`,
  which carry `{"closes_at": ...}` (`closing_field`).
- **Votes are events.** `board_poll_vote` names the board, the poll's root
  post (its Link content id, `board_polls.link_root_id`), the voter and the
  answers, signed by the voter's home node. This node keeps one vote per
  author identity and poll, the latest by `created_at`, and drops the
  signed bytes of a vote a later one replaced.
- **Bounded.** At most `MAX_VOTER_ROWS_PER_POLL` voters are kept per poll;
  a vote past that is turned away and counted in `dropped_votes`. The tally
  (`netbbs.boards.polls.tally`) counts at most
  `MAX_COUNTED_VOTERS_PER_NODE` voters from each other home node and
  leaves out votes whose author or home node trust hides, saying how many
  it left out.

A vote whose poll has not arrived yet is kept as a signed event and counted
once the poll's post is materialized (`project_retained_votes`).
"""

from __future__ import annotations

import json

from netbbs.auth.users import User
from netbbs.boards.boards import Board
from netbbs.boards.polls import (
    MAX_COUNTED_VOTERS_PER_NODE,
    MAX_POLL_OPTION_BYTES,
    MAX_VOTER_ROWS_PER_POLL,
    MAX_POLL_OPTIONS,
    MIN_POLL_OPTIONS,
    RESULTS_MODES,
    Poll,
    PollSpec,
    get_poll,
)
from netbbs.link.events import (
    BOARD_POLL_VOTE_OBJECT_TYPE,
    BoardPollVote,
    build_board_poll_vote,
)
from netbbs.link.node_identity import NodeIdentity
from netbbs.link.user_renames import current_user_name, former_user_names, is_retired_identity
from netbbs.storage.database import Database
from netbbs.timeutil import parse_utc_iso, utc_now_iso


def poll_field(poll: Poll | PollSpec) -> dict:
    """A poll as its `board_post` carries it."""
    return {
        "options": list(poll.options),
        "max_choices": poll.max_choices,
        "closes_at": poll.closes_at,
        "results": poll.results,
    }


def closing_field(poll: Poll) -> dict:
    """A poll's closing date as an edit of its post carries it."""
    return {"closes_at": poll.closes_at}


def _readable_time(value) -> bool:
    try:
        parse_utc_iso(value)
    except (TypeError, ValueError):
        return False
    return True


def carried_poll_spec(field) -> PollSpec | None:
    """The poll a carried `board_post`'s `"poll"` field describes, or `None`
    when it is not one this node can show -- the post is then shown without
    it, as a node that predates polls shows it. Unlike a poll written here,
    a carried one may already be past its closing date."""
    if not isinstance(field, dict):
        return None
    options = field.get("options")
    if not isinstance(options, list) or not MIN_POLL_OPTIONS <= len(options) <= MAX_POLL_OPTIONS:
        return None
    if any(
        not isinstance(o, str) or not o.strip() or len(o.encode("utf-8")) > MAX_POLL_OPTION_BYTES for o in options
    ):
        return None
    if len({o.strip().casefold() for o in options}) != len(options):
        return None
    max_choices = field.get("max_choices")
    if not isinstance(max_choices, int) or isinstance(max_choices, bool) or not 1 <= max_choices <= len(options):
        return None
    if field.get("results") not in RESULTS_MODES or not _readable_time(field.get("closes_at")):
        return None
    return PollSpec(tuple(o.strip() for o in options), max_choices, field["closes_at"], field["results"])


def write_carried_poll_without_commit(db: Database, content_id: str, field) -> bool:
    """Store the poll a carried root `board_post` (`content_id`, which is its
    local `post_id`) carries, if it carries a readable one."""
    spec = carried_poll_spec(field)
    if spec is None:
        return False
    db.connection.execute(
        "INSERT OR IGNORE INTO board_polls (root_post_id, max_choices, closes_at, results, link_root_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (content_id, spec.max_choices, spec.closes_at, spec.results, content_id),
    )
    db.connection.executemany(
        "INSERT OR IGNORE INTO board_poll_options (root_post_id, position, label) VALUES (?, ?, ?)",
        [(content_id, position, label) for position, label in enumerate(spec.options)],
    )
    return True


def apply_carried_closing_without_commit(db: Database, root_post_id: str, field) -> None:
    """A carried edit of a poll's post changes its closing date: the author's
    `board_post_edit` or the origin's `board_post_moderator_edit`. Anything
    else in the field is ignored -- a Linked poll's answers are fixed."""
    if not isinstance(field, dict) or not _readable_time(field.get("closes_at")):
        return
    db.connection.execute(
        "UPDATE board_polls SET closes_at = ? WHERE root_post_id = ?", (field["closes_at"], root_post_id)
    )


def mark_linked_without_commit(db: Database, root_post_id: str, link_root_id: str) -> None:
    """The poll of this node's own post `root_post_id` now travels as the
    signed `board_post` `link_root_id`: votes over the Link name it, and its
    answers are fixed from now on."""
    db.connection.execute(
        "UPDATE board_polls SET link_root_id = ? WHERE root_post_id = ? AND link_root_id IS NULL",
        (link_root_id, root_post_id),
    )


def queue_board_poll_vote_if_linked(
    db: Database, poll: Poll, board: Board, voter: User, *, node_identity: NodeIdentity
) -> BoardPollVote | None:
    """Sign `voter`'s stored vote on a Linked poll as a `board_poll_vote` and
    keep it on the vote's row, which `netbbs.link.sync` pushes and the
    board's inventory serves. A no-op returning `None` for a poll that does
    not travel."""
    if poll.link_root_id is None:
        return None
    row = db.connection.execute(
        "SELECT id, choices, voted_at FROM board_poll_votes WHERE root_post_id = ? AND user_id = ?",
        (poll.root_post_id, voter.id),
    ).fetchone()
    if row is None:
        return None
    vote = build_board_poll_vote(
        signing_identity=node_identity.signing_key,
        home_node_fingerprint=node_identity.fingerprint,
        local_user_id=voter.username,
        board_id=board.board_id,
        root_post_id=poll.link_root_id,
        choices=json.loads(row["choices"]),
        created_at=row["voted_at"],
    )
    db.connection.execute(
        "UPDATE board_poll_votes SET event_id = ?, event_json = ? WHERE id = ?",
        (vote.content_id, json.dumps(vote.to_dict()), row["id"]),
    )
    db.connection.commit()
    return vote


def own_vote_events(db: Database, *, board_local_id: int | None = None) -> list[BoardPollVote]:
    """This node's own signed votes, on every board it shows or on one."""
    board_sql = "AND b.id = ?" if board_local_id is not None else ""
    rows = db.connection.execute(
        f"""
        SELECT v.event_json FROM board_poll_votes v
        JOIN posts p ON p.post_id = v.root_post_id
        JOIN boards b ON b.id = p.board_id
        WHERE v.event_json IS NOT NULL AND b.link_hidden_at IS NULL {board_sql}
        """,
        (board_local_id,) if board_local_id is not None else (),
    ).fetchall()
    return [BoardPollVote.from_dict(json.loads(row["event_json"])) for row in rows]


def _keep_event_without_commit(db: Database, vote: BoardPollVote, sender_fingerprint: str) -> None:
    db.connection.execute(
        """
        INSERT INTO link_events (content_id, sender_fingerprint, object_type, envelope_json, received_at, board_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(content_id) DO NOTHING
        """,
        (
            vote.content_id, sender_fingerprint, BOARD_POLL_VOTE_OBJECT_TYPE, json.dumps(vote.to_dict()),
            utc_now_iso(), vote.payload["board_id"],
        ),
    )


def _vote_order(created_at: str, content_id: str) -> tuple:
    return parse_utc_iso(created_at), content_id


def _project_without_commit(db: Database, vote: BoardPollVote, root_post_id: str, sender_fingerprint: str) -> bool:
    """Count `vote` on the poll `root_post_id`: replace the voter's earlier
    vote if this one is later, keep the poll's voter rows bounded. Returns
    whether the vote's signed bytes are kept."""
    payload = vote.payload
    author = payload["author"]
    home = author["home_node_fingerprint"]
    # Issue #1306: a voter renamed on their home node is one person. Their
    # vote is kept under the name they go by now, replacing one kept under a
    # former name, so the latest wins across both.
    voter = current_user_name(db, home, author["local_user_id"])
    names = [voter, *former_user_names(db, home, voter)]
    marks = ",".join("?" * len(names))
    same_voter = db.connection.execute(
        f"SELECT id, voted_at, event_id FROM board_poll_votes "
        f"WHERE root_post_id = ? AND home_node_fingerprint = ? AND remote_user_id IN ({marks}) ORDER BY id",
        (root_post_id, home, *names),
    ).fetchall()
    existing = None
    for row in same_voter:
        if existing is None or _row_order(row) > _row_order(existing):
            if existing is not None:
                _drop_row_without_commit(db, existing)
            existing = row
        else:
            _drop_row_without_commit(db, row)
    choices = json.dumps(sorted(payload["choices"]))
    if existing is not None:
        if existing["event_id"] == vote.content_id:
            return True
        try:
            newer = _vote_order(payload["created_at"], vote.content_id) > _vote_order(
                existing["voted_at"], existing["event_id"] or ""
            )
        except ValueError:
            newer = True
        if not newer:
            # An older vote than the one counted: its bytes go, as a
            # replaced vote's do.
            db.connection.execute("DELETE FROM link_events WHERE content_id = ?", (vote.content_id,))
            return False
        db.connection.execute(
            "UPDATE board_poll_votes SET choices = ?, voted_at = ?, event_id = ?, remote_user_id = ? WHERE id = ?",
            (choices, payload["created_at"], vote.content_id, voter, existing["id"]),
        )
        if existing["event_id"]:
            db.connection.execute("DELETE FROM link_events WHERE content_id = ?", (existing["event_id"],))
    else:
        rows = db.connection.execute(
            "SELECT COUNT(*) FROM board_poll_votes WHERE root_post_id = ?", (root_post_id,)
        ).fetchone()[0]
        if rows >= MAX_VOTER_ROWS_PER_POLL:
            db.connection.execute(
                "UPDATE board_polls SET dropped_votes = dropped_votes + 1 WHERE root_post_id = ?", (root_post_id,)
            )
            db.connection.execute("DELETE FROM link_events WHERE content_id = ?", (vote.content_id,))
            return False
        db.connection.execute(
            "INSERT INTO board_poll_votes "
            "(root_post_id, home_node_fingerprint, remote_user_id, choices, voted_at, event_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (root_post_id, home, voter, choices, payload["created_at"], vote.content_id),
        )
    _keep_event_without_commit(db, vote, sender_fingerprint)
    return True


def _row_order(row) -> tuple:
    try:
        return parse_utc_iso(row["voted_at"]), row["event_id"] or ""
    except ValueError:
        return parse_utc_iso("1970-01-01T00:00:00Z"), row["event_id"] or ""


def _drop_row_without_commit(db: Database, row) -> None:
    """A voter's older vote under another of their names goes, with its
    signed bytes, as a replaced vote's do."""
    db.connection.execute("DELETE FROM board_poll_votes WHERE id = ?", (row["id"],))
    if row["event_id"]:
        db.connection.execute("DELETE FROM link_events WHERE content_id = ?", (row["event_id"],))


def _poll_for_link_root(db: Database, link_root_id: str) -> str | None:
    row = db.connection.execute(
        "SELECT root_post_id FROM board_polls WHERE link_root_id = ?", (link_root_id,)
    ).fetchone()
    return None if row is None else row["root_post_id"]


def materialize_carried_board_poll_vote(
    db: Database, vote: BoardPollVote, *, sender_fingerprint: str, own_fingerprint: str | None = None,
) -> bool:
    """Take in a received `board_poll_vote`. Returns whether its signed bytes
    are kept (to serve in the board's inventory): not for a board not
    carried or hidden here, a closed board, a voter the board's posting
    setting keeps out (as it would keep out their reply), this node's own
    vote coming back, or a vote older than the one this node counts for the
    same voter. A vote whose poll is not here yet is kept and counted when
    it arrives."""
    from netbbs.link.boards import _posting_allows

    payload = vote.payload
    board_row = db.connection.execute("SELECT * FROM boards WHERE board_id = ?", (payload["board_id"],)).fetchone()
    if board_row is None or board_row["link_hidden_at"] is not None or board_row["link_closed_at"] is not None:
        return False
    if own_fingerprint is not None and payload["author"]["home_node_fingerprint"] == own_fingerprint:
        return False
    if not _posting_allows(db, board_row, payload, is_reply=True):
        return False
    author = payload["author"]
    if is_retired_identity(db, author["home_node_fingerprint"], author["local_user_id"], payload.get("created_at")):
        # Issue #1306: signed under a name its home node had already renamed
        # away from -- refused as any new content under a retired name is.
        return False
    root_post_id = _poll_for_link_root(db, payload["root_post_id"])
    if root_post_id is None:
        _keep_event_without_commit(db, vote, sender_fingerprint)
        db.connection.commit()
        return True
    kept = _project_without_commit(db, vote, root_post_id, sender_fingerprint)
    db.connection.commit()
    return kept


def project_retained_votes_without_commit(db: Database, link_root_id: str, board_id: str) -> None:
    """Count the votes this node kept for the poll `link_root_id` before the
    poll's own post arrived, oldest first so the latest wins."""
    root_post_id = _poll_for_link_root(db, link_root_id)
    if root_post_id is None:
        return
    votes = []
    for row in db.connection.execute(
        "SELECT envelope_json, sender_fingerprint FROM link_events WHERE object_type = ? AND board_id = ?",
        (BOARD_POLL_VOTE_OBJECT_TYPE, board_id),
    ).fetchall():
        vote = BoardPollVote.from_dict(json.loads(row["envelope_json"]))
        if vote.payload.get("root_post_id") == link_root_id:
            votes.append((vote, row["sender_fingerprint"]))
    votes.sort(key=lambda item: (str(item[0].payload.get("created_at")), item[0].content_id))
    for vote, sender in votes:
        _project_without_commit(db, vote, root_post_id, sender)


__all__ = [
    "MAX_COUNTED_VOTERS_PER_NODE",
    "MAX_VOTER_ROWS_PER_POLL",
    "apply_carried_closing_without_commit",
    "carried_poll_spec",
    "closing_field",
    "get_poll",
    "mark_linked_without_commit",
    "materialize_carried_board_poll_vote",
    "own_vote_events",
    "poll_field",
    "project_retained_votes_without_commit",
    "queue_board_poll_vote_if_linked",
    "write_carried_poll_without_commit",
]
