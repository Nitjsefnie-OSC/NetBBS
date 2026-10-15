"""
Polls on message boards (issue #1299, built by #1332): a post with side
tables keyed by its root post. The domain half -- creating, voting,
counting, locking, closing and cleanup -- against a real SQLite file.
"""

from __future__ import annotations

import json

import pytest

from netbbs.auth.users import create_user, delete_user
from netbbs.boards import polls as polls_module
from netbbs.boards import posts as posts_module
from netbbs.boards.boards import create_board, delete_board
from netbbs.boards.polls import (
    PollError,
    PollSpec,
    answers_locked,
    cast_vote,
    close_poll,
    default_closes_at,
    get_poll,
    poll_closed,
    poll_hidden,
    tally,
    update_poll,
    user_vote,
)
from netbbs.boards.posts import (
    PostError,
    approve_post,
    create_post,
    delete_post,
    sweep_expired_posts,
    tombstone_post,
    withdraw_post,
)
from netbbs.ftn.address import FtnAddress
from netbbs.ftn.networks import FtnNetwork, save_network, set_board_area
from netbbs.moderation import BoardPermission, grant_permissions
from netbbs.storage.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "node.db")
    yield database
    database.close()


@pytest.fixture(autouse=True)
def _distinct_times(monkeypatch):
    """A fresh time for every post: two posts with the same text in the
    same clock tick would get the same content id."""
    ticks = iter(range(1, 100_000))
    monkeypatch.setattr(posts_module, "utc_now_iso", lambda: f"2026-10-15T10:00:00.{next(ticks):06d}Z")


def _user(db, name, level=10):
    return create_user(db, name, password="hunter2pw", user_level=level)


def _spec(*options, max_choices=1, days=7, results="after_vote"):
    return PollSpec(tuple(options or ("Yes", "No")), max_choices, default_closes_at(days), results)


def _poll_post(db, board, author, *options, **kwargs):
    post = create_post(db, board, author, "Best editor?", "Pick one.", poll=_spec(*options, **kwargs))
    return post, get_poll(db, post.root_post_id)


def _rows(db, table):
    return db.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# -- creating --------------------------------------------------------------------


def test_a_poll_is_a_post_with_its_answers_beside_it(db):
    alice = _user(db, "alice")
    board = create_board(db, "general", creator=alice)

    post, poll = _poll_post(db, board, alice, "vi", "emacs", "nano", max_choices=2)

    assert post.subject == "Best editor?" and post.body == "Pick one."
    assert poll.options == ("vi", "emacs", "nano")
    assert poll.max_choices == 2 and poll.results == "after_vote" and poll.closed_at is None
    assert not poll_closed(db, poll)
    assert tally(db, poll).counts == (0, 0, 0)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (PollSpec(("Only",), 1, None, "after_vote"), "at least 2 answers"),
        (PollSpec(tuple(str(i) for i in range(11)), 1, None, "after_vote"), "at most 10 answers"),
        (PollSpec(("Yes", " yes "), 1, None, "after_vote"), "there twice"),
        (PollSpec(("Yes", "  "), 1, None, "after_vote"), "blank"),
        (PollSpec(("Yes", "No"), 3, None, "after_vote"), "pick 1 to 2"),
        (PollSpec(("Yes", "No"), 1, "2020-01-01T00:00:00.000000Z", "after_vote"), "already passed"),
        (PollSpec(("Yes", "No"), 1, None, "whenever"), "unknown results"),
        (PollSpec(("Yes", "x" * 201), 1, None, "after_vote"), "too long"),
    ],
)
def test_a_poll_that_cannot_be_asked_is_refused_and_nothing_is_written(db, spec, message):
    alice = _user(db, "alice")
    board = create_board(db, "general", creator=alice)
    if spec.closes_at is None:
        spec = PollSpec(spec.options, spec.max_choices, default_closes_at(), spec.results)

    with pytest.raises(PollError, match=message):
        create_post(db, board, alice, "Q", "x", poll=spec)
    assert _rows(db, "posts") == 0 and _rows(db, "board_polls") == 0


def test_a_poll_closes_within_a_year(db):
    alice = _user(db, "alice")
    board = create_board(db, "general", creator=alice)
    with pytest.raises(PollError, match="within 365 days"):
        create_post(db, board, alice, "Q", "x", poll=_spec(days=400))


def test_a_reply_is_never_a_poll(db):
    alice = _user(db, "alice")
    board = create_board(db, "general", creator=alice)
    parent = create_post(db, board, alice, "Hello", "x")
    with pytest.raises(PollError, match="reply cannot be a poll"):
        create_post(db, board, alice, "Re: Hello", "y", parent_post_id=parent.post_id, poll=_spec())


def test_no_poll_on_a_board_gated_to_an_ftn_network(db):
    sysop = _user(db, "sysop", 255)
    network = save_network(db, FtnNetwork(
        name="fsxNet", domain="fsxnet", our_address=FtnAddress(21, 1, 999), uplink_address=FtnAddress(21, 1, 100),
        uplink_host="hub.example", session_password="SECRET", enabled=True,
    ))
    board = create_board(db, "general", creator=sysop)
    set_board_area(db, board, network.id, "FSX_GEN")
    with pytest.raises(PollError, match="FTN"):
        create_post(db, board, sysop, "Q", "x", poll=_spec())


# -- voting and counting -----------------------------------------------------------


def test_a_vote_is_counted_and_can_be_changed_until_close(db):
    alice, bob, carol = _user(db, "alice"), _user(db, "bob"), _user(db, "carol")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll_post(db, board, alice, "vi", "emacs", "nano")

    cast_vote(db, poll, board, bob, [0])
    cast_vote(db, poll, board, carol, [1])
    assert tally(db, poll).counts == (1, 1, 0) and tally(db, poll).voters == 2

    cast_vote(db, poll, board, bob, [2])  # changes it, does not add one
    result = tally(db, poll)
    assert result.counts == (0, 1, 1) and result.voters == 2
    assert user_vote(db, poll, bob) == (2,)
    assert user_vote(db, poll, alice) is None


def test_up_to_n_answers_and_never_more(db):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll_post(db, board, alice, "a", "b", "c", "d", max_choices=2)

    cast_vote(db, poll, board, bob, [3, 1])
    assert user_vote(db, poll, bob) == (1, 3)
    assert tally(db, poll).counts == (0, 1, 0, 1) and tally(db, poll).voters == 1
    with pytest.raises(PollError, match="at most 2"):
        cast_vote(db, poll, board, bob, [0, 1, 2])
    with pytest.raises(PollError, match="pick an answer"):
        cast_vote(db, poll, board, bob, [])
    with pytest.raises(PollError, match="no such answer"):
        cast_vote(db, poll, board, bob, [7])
    assert user_vote(db, poll, bob) == (1, 3)


def test_only_callers_who_may_post_vote(db):
    sysop, reader = _user(db, "sysop", 255), _user(db, "reader", 5)
    board = create_board(db, "members", creator=sysop, min_write_level=10)
    _post, poll = _poll_post(db, board, sysop)

    with pytest.raises(PollError, match="who may post"):
        cast_vote(db, poll, board, reader, [0])
    # A write grant opens it, as it opens posting.
    grant_permissions(
        db, reader, object_type="board", object_id=board.id, permissions=BoardPermission.WRITE, granted_by=sysop,
    )
    cast_vote(db, poll, board, reader, [0])
    assert tally(db, poll).voters == 1


def test_a_held_poll_takes_no_votes_until_it_is_approved(db):
    mod, alice, bob = _user(db, "mod", 255), _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "held", creator=mod, moderated=True)
    post, poll = _poll_post(db, board, alice)

    with pytest.raises(PollError, match="waits for a moderator"):
        cast_vote(db, poll, board, bob, [0])
    approve_post(db, post, approved_by=mod)
    cast_vote(db, poll, board, bob, [0])
    assert tally(db, poll).voters == 1


def test_a_rejected_poll_goes_with_its_post(db):
    mod, alice = _user(db, "mod", 255), _user(db, "alice")
    board = create_board(db, "held", creator=mod, moderated=True)
    post, _poll = _poll_post(db, board, alice)

    delete_post(db, post, deleted_by=mod)

    assert _rows(db, "board_polls") == 0 and _rows(db, "board_poll_options") == 0


# -- closing ---------------------------------------------------------------------


def test_the_author_and_a_moderator_close_a_poll_and_nobody_else(db):
    alice, bob, mod = _user(db, "alice"), _user(db, "bob"), _user(db, "mod")
    board = create_board(db, "general", creator=alice)
    grant_permissions(db, mod, object_type="board", object_id=board.id, permissions=BoardPermission.EDIT,
                      granted_by=_user(db, "root", 255))
    post, poll = _poll_post(db, board, alice)
    _post2, poll2 = _poll_post(db, board, alice, "x", "y")

    with pytest.raises(PollError, match="author or a moderator"):
        close_poll(db, poll, post.author_user_id, board, closed_by=bob)
    cast_vote(db, poll, board, bob, [1])
    close_poll(db, poll, post.author_user_id, board, closed_by=alice)
    close_poll(db, poll2, post.author_user_id, board, closed_by=mod)

    closed = get_poll(db, poll.root_post_id)
    assert poll_closed(db, closed)
    with pytest.raises(PollError, match="closed"):
        cast_vote(db, closed, board, bob, [0])
    # The vote cast before the close still counts.
    assert tally(db, closed).counts == (0, 1)
    with pytest.raises(PollError, match="already closed"):
        close_poll(db, closed, post.author_user_id, board, closed_by=alice)


def test_a_poll_past_its_closing_time_takes_no_votes_and_late_votes_are_not_counted(db, monkeypatch):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    _post, poll = _poll_post(db, board, alice)
    cast_vote(db, poll, board, bob, [0])
    # A vote dated after the close (as a carried one can be) is not counted.
    db.connection.execute(
        "INSERT INTO board_poll_votes (root_post_id, home_node_fingerprint, remote_user_id, choices, voted_at) "
        "VALUES (?, 'node', 'zed', ?, ?)",
        (poll.root_post_id, json.dumps([1]), "2099-01-01T00:00:00.000000Z"),
    )
    db.connection.commit()
    result = tally(db, poll)
    assert result.counts == (1, 0) and result.voters == 1 and result.not_counted == 1

    monkeypatch.setattr(polls_module, "utc_now_iso", lambda: "2099-06-01T00:00:00.000000Z")
    assert poll_closed(db, poll)
    with pytest.raises(PollError, match="closed"):
        cast_vote(db, poll, board, bob, [1])


def test_a_withdrawal_closes_the_poll_and_hides_its_totals(db):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    post, poll = _poll_post(db, board, alice)
    cast_vote(db, poll, board, bob, [0])

    withdraw_post(db, post, board, withdrawn_by=alice)

    poll = get_poll(db, poll.root_post_id)
    assert poll.closed_at is not None and poll_hidden(db, poll)
    with pytest.raises(PollError, match="closed"):
        cast_vote(db, poll, board, bob, [1])


def test_a_takedown_closes_the_poll_and_hides_its_totals(db):
    alice, mod = _user(db, "alice"), _user(db, "mod", 255)
    board = create_board(db, "general", creator=alice)
    post, poll = _poll_post(db, board, alice)

    tombstone_post(db, post, board, tombstoned_by=mod)

    poll = get_poll(db, poll.root_post_id)
    assert poll.closed_at is not None and poll_hidden(db, poll)


# -- changing ---------------------------------------------------------------------


def test_answers_lock_at_the_first_vote_but_the_closing_date_stays_editable(db):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=alice)
    post, poll = _poll_post(db, board, alice, "vi", "emacs")

    # Before anyone votes, everything can change.
    poll = update_poll(db, poll, post.author_user_id, board, _spec("vi", "emacs", "nano"), changed_by=alice)
    assert poll.options == ("vi", "emacs", "nano") and not answers_locked(db, poll)

    cast_vote(db, poll, board, bob, [2])
    assert answers_locked(db, poll)
    with pytest.raises(PollError, match="someone has voted"):
        update_poll(db, poll, post.author_user_id, board, _spec("vi", "emacs"), changed_by=alice)
    with pytest.raises(PollError, match="someone has voted"):
        update_poll(db, poll, post.author_user_id, board, _spec("vi", "emacs", "nano", max_choices=2),
                    changed_by=alice)

    later = default_closes_at(14)
    poll = update_poll(
        db, poll, post.author_user_id, board, PollSpec(poll.options, 1, later, "after_vote"), changed_by=alice,
    )
    assert poll.closes_at == later
    with pytest.raises(PollError, match="author or a moderator"):
        update_poll(db, poll, post.author_user_id, board, _spec("vi", "emacs", "nano", days=3), changed_by=bob)


# -- cleanup ----------------------------------------------------------------------


def test_an_expired_poll_is_swept_away_with_its_post(db, monkeypatch):
    alice, bob = _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "short", creator=alice, max_post_age_days=1)
    monkeypatch.setattr(posts_module, "utc_now_iso", lambda: "2020-01-01T00:00:00.000000Z")
    monkeypatch.setattr(polls_module, "utc_now_iso", lambda: "2020-01-01T00:00:00.000000Z")
    _post, poll = _poll_post(db, board, alice)
    cast_vote(db, poll, board, bob, [0])
    assert _rows(db, "board_poll_votes") == 1
    monkeypatch.undo()

    sweep_expired_posts(db, board)  # expired and past its grace: deleted

    assert _rows(db, "posts") == 0
    assert (_rows(db, "board_polls"), _rows(db, "board_poll_options"), _rows(db, "board_poll_votes")) == (0, 0, 0)


def test_a_deleted_board_takes_its_polls_and_a_deleted_account_its_vote(db):
    sysop, alice, bob = _user(db, "sysop", 255), _user(db, "alice"), _user(db, "bob")
    board = create_board(db, "general", creator=sysop)
    _post, poll = _poll_post(db, board, alice)
    cast_vote(db, poll, board, bob, [0])
    cast_vote(db, poll, board, alice, [1])

    delete_user(db, bob, deleted_by=sysop)
    assert tally(db, poll).counts == (0, 1)

    delete_board(db, board, deleted_by=sysop)
    assert (_rows(db, "board_polls"), _rows(db, "board_poll_options"), _rows(db, "board_poll_votes")) == (0, 0, 0)


def test_a_post_that_failed_to_write_leaves_no_poll(db):
    """The poll rows are written in the post's transaction: a refusal after
    the post's own insert leaves neither."""
    alice = _user(db, "alice")
    board = create_board(db, "general", creator=alice)
    spec = _spec()
    create_post(db, board, alice, "Q", "x", poll=spec)
    with pytest.raises(PostError):
        # The same text in the same instant: the content id clashes.
        posts_module.utc_now_iso = lambda: "2026-10-15T10:00:00.000001Z"
        create_post(db, board, alice, "Q", "x", poll=spec)
    assert _rows(db, "board_polls") == 1
